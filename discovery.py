# -*- coding: utf-8 -*-
"""服务器全自动发现：UDP 3483 广播优先（毫秒级）→ 本机网段并发扫描兜底
   命中第一台 LMS/Daphile 立即停止并返回，全程无交互。"""

import re
import json
import time
import socket
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from config import Config


class NetDiscovery:

    # ---------- 本机 IP ----------
    @staticmethod
    def local_ips():
        ips = set()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
            s.close()
        except OSError:
            pass
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if ip.startswith(("192.168.", "10.", "172.")):
                    ips.add(ip)
        except OSError:
            pass
        return sorted(ips)

    @staticmethod
    def subnet_of(ip):
        return ip.rsplit(".", 1)[0] + "."

    # ---------- 第一步：UDP 广播（命中即返回） ----------
    @staticmethod
    def udp_discover(timeout=1.5):
        """向 3483 端口广播 Slimproto 发现包 'd'，收到 LMS/Daphile 应答即停"""
        found = {}
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            targets = [("255.255.255.255", 3483)]
            for ip in NetDiscovery.local_ips():
                targets.append((NetDiscovery.subnet_of(ip) + "255", 3483))
            for t in targets:
                try:
                    sock.sendto(b"d", t)
                except OSError:
                    pass
            sock.settimeout(0.3)
            end = time.time() + timeout
            while time.time() < end:
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                if data and data[0:1] == b"E":          # 服务器应答
                    info = NetDiscovery._parse_discover(data)
                    if info:
                        found[info["ip"]] = info
                        break                            # ★ 命中第一台立即停止
        except OSError:
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        return list(found.values())

    @staticmethod
    def _parse_discover(data):
        # 应答格式: 'E' + NAME..IPAD..JSON..（以 \x00/\n 分隔）
        text = data[1:].decode("utf-8", "ignore")
        info = {"ip": "", "name": "", "port": 9000}
        for field in re.split(r"[\x00\n\r]+", text):
            m = re.match(r"^(NAME|IPAD|JSON)(.*)$", field)
            if not m:
                continue
            k, v = m.group(1), m.group(2)
            if k == "NAME":
                info["name"] = v
            elif k == "IPAD":
                info["ip"] = v
            elif k == "JSON":
                try:
                    info["port"] = int(v)
                except ValueError:
                    pass
        if info["ip"] and not info["name"]:
            info["name"] = info["ip"]
        return info if info["ip"] else None

    # ---------- 第二步：仅扫描本机所在网段，命中第一台即停 ----------
    @staticmethod
    def build_ip_list():
        """由本机 IP 推导网段（如本机 192.168.3.23 → 扫 192.168.3.1~254）"""
        ips = []
        for lip in NetDiscovery.local_ips():
            base = NetDiscovery.subnet_of(lip)
            ips += [base + str(i) for i in range(1, 255)]
        return list(dict.fromkeys(ips))

    @staticmethod
    def auto_scan(ip_list, ports=None, timeout=0.3, workers=150):
        """并发扫描，验证到第一台 LMS/Daphile 立即返回 (ip, port, name)"""
        ports = ports or Config.SCAN_PORTS
        stop = threading.Event()
        result = []

        def check(ip, port):
            if stop.is_set():
                return None
            if not NetDiscovery._tcp_connect(ip, port, timeout):
                return None
            verified = NetDiscovery._verify_lms(ip, port)
            if verified and not stop.is_set():
                stop.set()                               # ★ 命中 → 全部线程收工
                result.append(verified)
            return verified

        tasks = [(ip, p) for ip in ip_list for p in ports]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(check, ip, p) for ip, p in tasks]
            while not stop.is_set():                     # 命中即返回，不等扫完
                if all(f.done() for f in futs):
                    break
                time.sleep(0.05)
            stop.set()
            for f in futs:
                f.cancel()
        return result[0] if result else None

    @staticmethod
    def _tcp_connect(ip, port, timeout):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return True
        except OSError:
            return False

    @staticmethod
    def _verify_lms(ip, port):
        """JSON-RPC 验证是 LMS/Daphile（而非普通 web 服务）"""
        try:
            payload = {"method": "slim.request",
                       "params": ["-", ["serverstatus", "0", "1"]]}
            req = urllib.request.Request(
                f"http://{ip}:{port}/jsonrpc.js",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                json.loads(resp.read().decode())
            return (ip, port, ip)
        except Exception:
            return None

    # ---------- 对外总入口 ----------
    @classmethod
    def auto_discover(cls):
        """返回 (ip, port) 或 None"""
        print("[发现] UDP 广播探测中…")
        for info in cls.udp_discover(1.5):
            print(f"[发现] UDP 命中: {info['name']} @ {info['ip']}")
            return info["ip"], info["port"]
        ips = cls.build_ip_list()
        print(f"[发现] UDP 未命中，自动扫描本机网段 {len(ips)} 个地址 "
              f"({', '.join(map(str, Config.SCAN_PORTS))} 端口)…")
        hit = cls.auto_scan(ips)
        if hit:
            print(f"[发现] TCP 命中: {hit[2]} @ {hit[0]}:{hit[1]}")
            return hit[0], hit[1]
        print("[发现] 未找到 Daphile/LMS 服务器")
        return None
