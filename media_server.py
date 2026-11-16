# -*- coding: utf-8 -*-
"""本机媒体中转服务：把本地音乐文件通过 HTTP 提供给达菲（LMS）拉流播放。

用途：当前扬声器选中「达菲设备」时，点本地音乐里的歌 →
     得到 http://<本机局域网IP>:<端口>/local/<曲目id>/<歌名>.<扩展名>
     再让 LMS 播放这个地址，达菲设备就能播出电脑里的文件。
支持 Range 请求，达菲可以边下边播、也能拖动进度。
"""

import http.server
import os
import socket
import threading
import urllib.parse

_MIME = {
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wma": "audio/x-ms-wma",
}


def lan_ip():
    """本机在局域网里的 IPv4（不发包，只查路由表）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return ""


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "DaphilePlayerMedia/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self._serve(head_only=True)

    def do_GET(self):
        self._serve(head_only=False)

    def _serve(self, head_only=False):
        library = getattr(self.server, "library", None)
        if library is None:
            self.send_error(503, "no library")
            return
        parsed = urllib.parse.urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2 or parts[0] != "local":
            self.send_error(404, "not found")
            return
        tid = urllib.parse.unquote(parts[1])
        track = library.get(tid)
        path = (track or {}).get("path") or ""
        if not track or not os.path.isfile(path):
            self.send_error(404, "track not found")
            return

        size = os.path.getsize(path)
        ext = os.path.splitext(path)[1].lower()
        ctype = _MIME.get(ext, "application/octet-stream")

        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                s, _, e = rng[6:].partition("-")
                if s:
                    start = int(s)
                if e:
                    end = int(e)
            except ValueError:
                start, end = 0, size - 1
        if start < 0:
            start = 0
        if end >= size:
            end = size - 1
        if start > end:
            self.send_error(416, "bad range")
            return

        length = end - start + 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if rng:
            self.send_header("Content-Range",
                             f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head_only:
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


class MediaServer:
    """轻量 HTTP 文件服务，端口自动分配，进程内全局复用。"""

    def __init__(self, library):
        self._library = library
        self._httpd = None
        self._thread = None
        self.port = 0

    def start(self):
        if self._httpd is not None:
            return True
        try:
            httpd = http.server.ThreadingHTTPServer(("0.0.0.0", 0), _Handler)
        except OSError as e:
            print(f"[本地推流] 启动 HTTP 服务失败: {e}")
            return False
        httpd.library = self._library
        httpd.daemon_threads = True
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(target=httpd.serve_forever,
                                        daemon=True)
        self._thread.start()
        print(f"[本地推流] HTTP 服务已启动，端口 {self.port}")
        return True

    def stop(self):
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass

    def url_for(self, tid, title="", ext=""):
        if not self.start():
            return ""
        ip = lan_ip()
        if not ip:
            return ""
        name = urllib.parse.quote(str(title or tid))
        if ext and not ext.startswith("."):
            ext = "." + ext
        return f"http://{ip}:{self.port}/local/{urllib.parse.quote(tid)}/{name}{ext}"
