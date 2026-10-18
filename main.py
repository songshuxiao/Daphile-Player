# -*- coding: utf-8 -*-
"""程序入口：加载配置 →
   ① LAST_IP 快速探测（0.8 秒端口探测 + 取 players）
   ② 成功 → 立即进主界面
   ③ 失败 → 立即弹连接对话框（手动输入 / 远程唤醒 / 重新扫描）
   ④ 连接成功后自动记忆主机 MAC 供下次远程唤醒
   ⑤ 关机后 → 弹连接对话框重连"""

import os
import sys
import socket
import threading
import tkinter as tk
from tkinter import messagebox
import atexit

from config import Config
from discovery import NetDiscovery
from lms_client import LmsClient
from lyrics_fetcher import LyricsFetcher
from karaoke_overlay import KaraokeOverlay
from control_panel import ControlPanel


def _resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _apply_icon(window):
    ico = _resource_path("icon.ico")
    if os.path.isfile(ico):
        try:
            window.iconbitmap(ico)
        except tk.TclError:
            pass


# =========================================================
# 主机 MAC 记忆
# =========================================================
def _remember_host_mac(cfg, host, players, lms_client=None):
    """记忆主机 MAC，优先级：
         ① LMS API serverstatus（最权威：拿到有效值就覆盖旧配置）
         ② playerid 兜底（仅当配置为空时才使用）"""
    cur = (getattr(cfg, "HOST_MAC", "") or "").strip()

    api_mac = ""
    if lms_client is not None:
        try:
            api_mac = lms_client.get_server_mac() or ""
        except Exception as e:
            print(f"[WOL] LMS API 获取 MAC 异常: {e}")
            api_mac = ""

    if api_mac:
        if api_mac.lower() == cur.lower():
            print(f"[WOL] 主机 MAC 未变: {cur}")
            return
        cfg.HOST_MAC = api_mac
        try:
            cfg.save()
        except Exception:
            pass
        print(f"[WOL] 已更新主机 MAC: {cur or '(空)'} → {api_mac}")
        return

    if cur:
        print(f"[WOL] API 未返回 MAC，保留当前配置: {cur}")
        return

    if players and players[0]:
        raw = str(players[0][0] or "")
        clean = raw.replace(":", "").replace("-", "").replace(" ", "").strip()
        if (len(clean) == 12
                and all(c in "0123456789abcdefABCDEF" for c in clean)):
            mac = ":".join(clean[i:i + 2] for i in range(0, 12, 2)).lower()
            cfg.HOST_MAC = mac
            try:
                cfg.save()
            except Exception:
                pass
            print(f"[WOL] 已从 playerid 兜底记忆 MAC（虚拟值）: {mac}")
            return

    print("[WOL] 未能自动获取主机 MAC，需在连接对话框中手动填写")


# =========================================================
# LAST_IP 快速探测
# =========================================================
def _port_open(host, port, timeout=0.8):
    """快速探测 TCP 端口是否开放"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _try_last_ip(cfg, timeout=0.8):
    """快速尝试 LAST_IP。成功返回 (host, port, players)，失败立即返回 None。"""
    last_ip = (getattr(cfg, "LAST_IP", "") or "").strip()
    if not last_ip:
        print("[启动] 无 LAST_IP，跳过快速探测")
        return None

    print(f"[启动] 探测 LAST_IP {last_ip}:9000（超时 {timeout}s）…")
    if not _port_open(last_ip, 9000, timeout=timeout):
        print(f"[启动] {last_ip}:9000 端口关闭，快速探测失败")
        return None

    print(f"[启动] {last_ip}:9000 可达，取 players…")
    try:
        test = LmsClient(last_ip, 9000, cfg.PLAYER_MAC)
        players = test.list_players()
        if players:
            print(f"[启动] ✓ 快速连接成功: {last_ip}:9000")
            return last_ip, 9000, players
        print("[启动] 服务器上无可用 player")
    except Exception as e:
        print(f"[启动] 快速连接失败: {e}")
    return None


# =========================================================
# 连接对话框
# =========================================================
class ConnectDialog:

    BG     = "#15151E"
    CARD   = "#20202E"
    FIELD  = "#1A1A24"
    FG     = "#E8E8F0"
    DIM    = "#9A9AB0"
    ACCENT = "#5A5AE0"
    ERR    = "#FF8080"

    def __init__(self, parent, cfg):
        self.cfg = cfg
        self.result = None
        self._busy = False
        self._scanning = False
        self._canceled = False

        self.win = tk.Toplevel(parent)
        self.win.title("连接 Daphile")
        self.win.geometry("480x400")
        self.win.configure(bg=self.BG)
        self.win.attributes("-topmost", True)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self._cancel)
        _apply_icon(self.win)

        self.win.update_idletasks()
        w, h = 480, 400
        x = (self.win.winfo_screenwidth() - w) // 2
        y = (self.win.winfo_screenheight() - h) // 2
        self.win.geometry(f"{w}x{h}+{x}+{y}")

        self._build_ui()
        self.win.after(50, lambda: self.ent_ip.focus_set())

    def _build_ui(self):
        p = self.win

        tk.Label(p, text="连接 Daphile / LMS 服务器",
                 bg=self.BG, fg=self.FG,
                 font=("Microsoft YaHei UI", 14, "bold")).pack(pady=(20, 4))
        tk.Label(p, text="未找到服务器 —— 请手动输入 IP，或点击「重新扫描」",
                 bg=self.BG, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9)).pack(pady=(0, 16))

        r1 = tk.Frame(p, bg=self.BG)
        r1.pack(fill="x", padx=20, pady=4)
        tk.Label(r1, text="服务器 IP", bg=self.BG, fg=self.FG,
                 font=("Microsoft YaHei UI", 10), width=10,
                 anchor="w").pack(side="left")
        self.ent_ip = tk.Entry(r1, bg=self.FIELD, fg=self.FG,
                               insertbackground=self.FG, relief="flat",
                               font=("Consolas", 11))
        self.ent_ip.pack(side="left", fill="x", expand=True, ipady=4, padx=4)
        self.ent_ip.insert(0, getattr(self.cfg, "LAST_IP", ""))
        self.ent_ip.bind("<Return>", lambda _e: self._try_connect())
        self.btn_connect = self._mk_btn(r1, "连接", self._try_connect,
                                        primary=True)
        self.btn_connect.pack(side="left", padx=(4, 0))

        r2 = tk.Frame(p, bg=self.BG)
        r2.pack(fill="x", padx=20, pady=4)
        tk.Label(r2, text="主机 MAC", bg=self.BG, fg=self.FG,
                 font=("Microsoft YaHei UI", 10), width=10,
                 anchor="w").pack(side="left")
        self.ent_mac = tk.Entry(r2, bg=self.FIELD, fg=self.FG,
                                insertbackground=self.FG, relief="flat",
                                font=("Consolas", 11))
        self.ent_mac.pack(side="left", fill="x", expand=True, ipady=4, padx=4)
        self.ent_mac.insert(0, getattr(self.cfg, "HOST_MAC", ""))
        self.btn_wol = self._mk_btn(r2, "远程唤醒", self._wake,
                                    primary=True)
        self.btn_wol.pack(side="left", padx=(4, 0))

        r3 = tk.Frame(p, bg=self.BG)
        r3.pack(fill="x", padx=20, pady=(10, 8))
        self.btn_rescan = self._mk_btn(r3, "重新扫描", self._rescan)
        self.btn_rescan.pack(side="left")
        # 取消按钮已移除，用户通过窗口右上角 × 关闭

        tk.Label(p, text="提示：唤醒后请等待 30~60 秒，再点「连接」或「重新扫描」",
                 bg=self.BG, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9),
                 wraplength=440, justify="left").pack(fill="x", padx=20,
                                                      pady=(0, 4))

        self.lbl_status = tk.Label(p, text="",
                                    bg=self.BG, fg=self.DIM,
                                    font=("Microsoft YaHei UI", 9),
                                    wraplength=440, justify="left")
        self.lbl_status.pack(fill="x", padx=20)

    def _mk_btn(self, parent, text, cmd, primary=False):
        if primary:
            bg = self.ACCENT
            fg = "#FFFFFF"
            active_bg = "#7A7AFF"
            active_fg = "#FFFFFF"
            font_style = ("Microsoft YaHei UI", 10, "bold")
            padx, pady = 14, 4
            disabled_fg = "#C8C8E0"
        else:
            bg = self.CARD
            fg = self.FG
            active_bg = self.ACCENT
            active_fg = "#FFFFFF"
            font_style = ("Microsoft YaHei UI", 10)
            padx, pady = 10, 3
            disabled_fg = "#808090"

        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg,
                         activebackground=active_bg,
                         activeforeground=active_fg,
                         disabledforeground=disabled_fg,
                         relief="flat", font=font_style,
                         padx=padx, pady=pady, cursor="hand2")

    def _set_status(self, text, color=None):
        try:
            self.lbl_status.config(text=text, fg=color or self.DIM)
        except (tk.TclError, RuntimeError):
            pass

    def _set_busy(self, busy):
        self._busy = busy
        st = "disabled" if busy else "normal"
        for b in (self.btn_connect, self.btn_wol, self.btn_rescan):
            try:
                b.config(state=st)
            except (tk.TclError, RuntimeError):
                pass

    def _win_alive(self):
        """窗口是否存活。★ 只能在主线程调用。"""
        if self._canceled:
            return False
        try:
            return bool(self.win.winfo_exists())
        except (tk.TclError, RuntimeError):
            return False

    def _safe_after(self, delay, func):
        """跨线程安全调度回主线程。"""
        try:
            self.win.after(delay, func)
        except (tk.TclError, RuntimeError) as e:
            print(f"[对话框] after 调度失败: {e}")

    def _try_connect(self):
        if self._busy:
            return
        ip = self.ent_ip.get().strip()
        if not ip:
            self._set_status("请输入服务器 IP", self.ERR)
            return
        parts = ip.split(".")
        if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255
                                       for p in parts):
            self._set_status(f"IP 格式不正确：{ip}", self.ERR)
            return

        self._set_busy(True)
        self._set_status(f"正在连接 {ip}:9000 …", self.ACCENT)

        # 5 秒后仍未返回，显示"响应较慢"提示
        def slow_hint():
            if not self._busy or not self._win_alive():
                return
            self._set_status(
                f"正在连接 {ip}:9000 …（响应较慢，请稍候）",
                self.ACCENT)
        self._safe_after(5000, slow_hint)

        def work():
            # ① 先快速探测端口（IP 错误 2 秒内失败）
            if not _port_open(ip, 9000, timeout=2.0):
                def fail_port():
                    if not self._win_alive():
                        return
                    self._set_busy(False)
                    self._set_status(
                        f"无法连接 {ip}:9000（端口不可达，请检查 IP 是否正确）",
                        self.ERR)
                self._safe_after(0, fail_port)
                return

            # ② 端口通，取 players
            ok, err, players, client = False, "", [], None
            try:
                client = LmsClient(ip, 9000, self.cfg.PLAYER_MAC)
                players = client.list_players()
                if players:
                    ok = True
                else:
                    err = "服务器上没有可用的播放器"
            except Exception as e:
                err = str(e)

            def apply():
                if not self._win_alive():
                    return
                if ok:
                    self.cfg.LAST_IP = ip
                    try:
                        self.cfg.save()
                    except Exception:
                        pass
                    _remember_host_mac(self.cfg, ip, players,
                                       lms_client=client)
                    self.result = (ip, 9000)
                    try:
                        self.win.destroy()
                    except tk.TclError:
                        pass
                else:
                    self._set_busy(False)
                    self._set_status(f"连接失败：{err}", self.ERR)

            self._safe_after(0, apply)

        threading.Thread(target=work, daemon=True).start()

    def _wake(self):
        if self._busy:
            return
        mac = self.ent_mac.get().strip()
        if not mac:
            self._set_status("请先填写主机 MAC 地址（Daphile 网卡的 MAC）",
                             self.ERR)
            return

        self.cfg.HOST_MAC = mac
        try:
            self.cfg.save()
        except Exception:
            pass

        ip = self.ent_ip.get().strip() or getattr(self.cfg, "LAST_IP", "")
        if ip.count(".") == 3:
            broadcast = ip.rsplit(".", 1)[0] + ".255"
        else:
            broadcast = "255.255.255.255"

        ok = LmsClient.send_wol(mac, ip=broadcast, port=9)
        if ok:
            self._set_status(
                f"魔术包已发送 (MAC={mac}, 广播={broadcast})\n"
                f"请等待 30~60 秒设备启动，再点「连接」或「重新扫描」",
                self.ACCENT)
        else:
            self._set_status("魔术包发送失败，请检查 MAC 格式与网络", self.ERR)

    def _rescan(self):
        """扫描网络。★ 后台线程绝不碰 tk 对象，UI 全部走 _safe_after。"""
        if self._scanning:
            return
        self._scanning = True
        try:
            self.btn_rescan.config(state="disabled", text="扫描中…")
        except (tk.TclError, RuntimeError):
            pass
        self._set_status("正在扫描网络…", self.ACCENT)

        def work():
            discovered = None
            try:
                discovered = NetDiscovery.auto_discover()
                print(f"[对话框] 扫描完成，结果: {discovered}")
            except Exception as e:
                print(f"[对话框] 扫描异常: {e}")

            # ★ 直接调度回主线程，不在后台线程检查窗口
            def apply():
                if not self._win_alive():
                    print("[对话框] 窗口已关闭，丢弃扫描结果")
                    return
                self._scanning = False
                try:
                    self.btn_rescan.config(state="normal", text="重新扫描")
                except (tk.TclError, RuntimeError):
                    pass
                if discovered:
                    print(f"[对话框] 扫描命中: {discovered}")
                    self.result = discovered
                    try:
                        self.win.destroy()
                    except tk.TclError:
                        pass
                else:
                    self._set_status(
                        "未找到服务器，请手动输入 IP 或点击「远程唤醒」",
                        self.ERR)

            self._safe_after(0, apply)

        threading.Thread(target=work, daemon=True).start()

    def _cancel(self):
        self._canceled = True
        self.result = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def wait(self):
        self.win.wait_window()
        return self.result


# =========================================================
# 达菲关机后重连流程
# =========================================================
def _handle_daphile_shutdown(cfg, overlay, panel):
    print("[关机] 达菲正在关机，隐藏主界面并弹出重连对话框…")

    try:
        panel.panel.withdraw()
    except tk.TclError:
        pass
    try:
        overlay.hide()
    except tk.TclError:
        pass

    dlg = ConnectDialog(overlay.root, cfg)
    result = dlg.wait()

    if not result:
        print("[关机] 用户取消重连，退出程序")
        try:
            overlay.root.destroy()
        except tk.TclError:
            pass
        return

    host, port = result
    print(f"[关机] 选择重连: {host}:{port}")

    try:
        new_lms = LmsClient(host, port, cfg.PLAYER_MAC)
        players = new_lms.list_players()
        if not players:
            messagebox.showerror("错误", "服务器上未发现任何 player")
            overlay.root.destroy()
            return
    except Exception as e:
        messagebox.showerror("连接失败", f"无法连接 {host}:{port}\n{e}")
        overlay.root.destroy()
        return

    cfg.LAST_IP = host
    try:
        cfg.save()
    except Exception:
        pass
    _remember_host_mac(cfg, host, players, lms_client=new_lms)

    new_fetcher = LyricsFetcher(new_lms)
    panel.reconnect(
        new_lms, new_fetcher,
        players[0][1] if players else "",
        f"服务器: {host}:{port}")

    print("[关机] 重连成功，主界面已恢复")


# =========================================================
# 主流程
# =========================================================
def main():
    cfg = Config()
    cfg.load()

    overlay = KaraokeOverlay(cfg)
    _apply_icon(overlay.root)
    overlay.hide()

    print("[启动] 快速探测 LAST_IP…")
    result = _try_last_ip(cfg)

    if result is not None:
        host, port, players = result
    else:
        print("[启动] LAST_IP 未命中，立即弹出连接对话框")
        dlg = ConnectDialog(overlay.root, cfg)
        discovered = dlg.wait()
        if not discovered:
            print("[启动] 用户取消，退出")
            overlay.root.destroy()
            return
        host, port = discovered
        try:
            test = LmsClient(host, port, cfg.PLAYER_MAC)
            players = test.list_players()
            if not players:
                messagebox.showerror("错误", "服务器上未发现任何 player")
                overlay.root.destroy()
                return
        except Exception as e:
            messagebox.showerror("连接失败", f"无法连接 {host}:{port}\n{e}")
            overlay.root.destroy()
            return

    cfg.LAST_IP = host
    try:
        cfg.save()
    except Exception:
        pass

    lms = LmsClient(host, port, cfg.PLAYER_MAC)
    _remember_host_mac(cfg, host, players, lms_client=lms)

    fetcher = LyricsFetcher(lms)
    panel = ControlPanel(lms, overlay, fetcher, cfg)
    _apply_icon(panel.panel)
    panel.player_name = players[0][1] if players else ""
    panel.lbl_server.config(text=f"服务器: {host}:{port}")

    panel.on_daphile_shutdown = lambda: _handle_daphile_shutdown(
        cfg, overlay, panel)

    def on_close():
        try:
            cfg.OVERLAY_X, cfg.OVERLAY_Y = overlay.get_position()
        except Exception:
            pass
        cfg.save()
        try:
            overlay.root.destroy()
        except Exception:
            pass

    atexit.register(cfg.save)
    overlay.root.protocol("WM_DELETE_WINDOW", on_close)
    panel.panel.protocol("WM_DELETE_WINDOW", on_close)

    overlay.show()
    overlay.run()


if __name__ == "__main__":
    main()