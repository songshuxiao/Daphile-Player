# -*- coding: utf-8 -*-
"""程序入口：加载配置 →
   ① 优先用上次成功的 IP 快速连接（秒进主界面，不依赖自动发现）
   ② 快速连接失败 → 自动发现
   ③ 自动发现失败 → 弹出连接对话框（手动 IP / 远程唤醒 / 重新发现）
   ④ 连接成功后自动记忆主机 MAC 供下次远程唤醒"""

import os
import sys
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
         ① LMS API serverstatus（最权威）
         ② playerid 兜底（可能是虚拟 MAC，不可靠）

       若 cfg.HOST_MAC 已有值：
         · 与 playerid 相同 → 判定为旧版兜底值，允许 API 覆盖
         · 其他值 → 视为用户手动填写，不覆盖
    """
    cur = (getattr(cfg, "HOST_MAC", "") or "").strip()

    # 先算出 playerid 对应的 MAC（如果有）
    pid_mac = ""
    if players and players[0]:
        raw = str(players[0][0] or "")
        clean = raw.replace(":", "").replace("-", "").replace(" ", "").strip()
        if (len(clean) == 12
                and all(c in "0123456789abcdefABCDEF" for c in clean)):
            pid_mac = ":".join(clean[i:i + 2]
                              for i in range(0, 12, 2)).lower()

    # 用户手动填的值（≠ playerid）→ 保留，不覆盖
    if cur and cur.lower() != pid_mac:
        print(f"[WOL] 保留用户手动设置的 MAC: {cur}")
        return

    # ① 尝试从 LMS API 获取
    if lms_client is not None:
        try:
            mac = lms_client.get_server_mac()
        except Exception as e:
            print(f"[WOL] LMS API 获取 MAC 异常: {e}")
            mac = ""
        if mac:
            cfg.HOST_MAC = mac
            try:
                cfg.save()
            except Exception:
                pass
            print(f"[WOL] 已从 LMS API 记忆主机 MAC: {mac}")
            return

    # ② 兜底：playerid
    if pid_mac:
        cfg.HOST_MAC = pid_mac
        try:
            cfg.save()
        except Exception:
            pass
        print(f"[WOL] LMS API 无结果，兜底使用 playerid MAC: {pid_mac}")
        return

    print("[WOL] 未能自动获取主机 MAC，需在连接对话框中手动填写")


# =========================================================
# 快速连接：直接用上次 IP，不等自动发现
# =========================================================
def _try_quick_connect(cfg):
    """尝试用 cfg.LAST_IP 快速连接。成功返回 (ip, port, players)，否则 None。"""
    ip = (getattr(cfg, "LAST_IP", "") or "").strip()
    if not ip:
        return None
    print(f"[启动] 尝试快速连接上次 IP: {ip}:9000")
    try:
        test = LmsClient(ip, 9000, cfg.PLAYER_MAC)
        players = test.list_players()
        if players:
            print(f"[启动] 快速连接成功，players={len(players)}")
            return ip, 9000, players
        print("[启动] 快速连接：服务器上无可用 player")
    except Exception as e:
        print(f"[启动] 快速连接失败: {e}")
    return None


# =========================================================
# 连接对话框：自动发现失败时使用
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
        tk.Label(p, text="自动发现失败 —— 请手动输入 IP，或先远程唤醒设备",
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
        self.btn_connect = self._mk_btn(r1, "连接", self._try_connect)
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
        self.btn_wol = self._mk_btn(r2, "远程唤醒", self._wake)
        self.btn_wol.pack(side="left", padx=(4, 0))

        r3 = tk.Frame(p, bg=self.BG)
        r3.pack(fill="x", padx=20, pady=(10, 8))
        self.btn_rescan = self._mk_btn(r3, "重新自动发现", self._rescan)
        self.btn_rescan.pack(side="left")
        tk.Button(r3, text="取消", command=self._cancel,
                  bg=self.BG, fg=self.DIM, relief="flat",
                  activebackground=self.CARD, activeforeground=self.FG,
                  font=("Microsoft YaHei UI", 10), padx=12, pady=3,
                  cursor="hand2").pack(side="right")

        tk.Label(p, text="提示：唤醒后请等待 30~60 秒，再点「连接」或「重新自动发现」",
                 bg=self.BG, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9),
                 wraplength=440, justify="left").pack(fill="x", padx=20, pady=(0, 4))

        self.lbl_status = tk.Label(p, text="",
                                    bg=self.BG, fg=self.DIM,
                                    font=("Microsoft YaHei UI", 9),
                                    wraplength=440, justify="left")
        self.lbl_status.pack(fill="x", padx=20)

    def _mk_btn(self, parent, text, cmd):
        return tk.Button(parent, text=text, command=cmd,
                         bg=self.CARD, fg=self.FG,
                         activebackground=self.ACCENT,
                         activeforeground="#FFFFFF",
                         relief="flat", font=("Microsoft YaHei UI", 10),
                         padx=10, pady=3, cursor="hand2")

    def _set_status(self, text, color=None):
        try:
            self.lbl_status.config(text=text, fg=color or self.DIM)
        except tk.TclError:
            pass

    def _set_busy(self, busy):
        self._busy = busy
        st = "disabled" if busy else "normal"
        for b in (self.btn_connect, self.btn_wol, self.btn_rescan):
            try:
                b.config(state=st)
            except tk.TclError:
                pass

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

        def work():
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
                if ok:
                    self.cfg.LAST_IP = ip
                    try:
                        self.cfg.save()
                    except Exception:
                        pass
                    # ★ 传入 client，让 MAC 记忆优先走 LMS API
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
            try:
                self.win.after(0, apply)
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _wake(self):
        if self._busy:
            return
        mac = self.ent_mac.get().strip()
        if not mac:
            self._set_status("请先填写主机 MAC 地址（Daphile 网卡的 MAC）", self.ERR)
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
                f"请等待 30~60 秒设备启动，再点「连接」或「重新自动发现」",
                self.ACCENT)
        else:
            self._set_status("魔术包发送失败，请检查 MAC 格式与网络", self.ERR)

    def _rescan(self):
        if self._busy:
            return
        self._set_busy(True)
        self._set_status("正在自动发现…", self.ACCENT)

        def work():
            discovered = NetDiscovery.auto_discover()

            def apply():
                if discovered:
                    self.result = discovered
                    try:
                        self.win.destroy()
                    except tk.TclError:
                        pass
                else:
                    self._set_busy(False)
                    self._set_status("自动发现失败，请手动输入 IP 或先远程唤醒设备",
                                     self.ERR)
            try:
                self.win.after(0, apply)
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _cancel(self):
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
    """达菲关机成功后：
       ① 隐藏主面板与浮窗
       ② 弹出连接对话框（可手动 IP / 远程唤醒 / 重新发现）
       ③ 用户取消 → 退出程序
       ④ 重连成功 → 更新 panel 内部引用并恢复界面"""
    print("[关机] 达菲正在关机，隐藏主界面并弹出重连对话框…")

    # ① 隐藏界面
    try:
        panel.panel.withdraw()
    except tk.TclError:
        pass
    try:
        overlay.hide()
    except tk.TclError:
        pass

    # ② 弹连接对话框
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

    # ③ 建立新连接
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

    # ④ 更新配置 + MAC 记忆
    cfg.LAST_IP = host
    try:
        cfg.save()
    except Exception:
        pass
    _remember_host_mac(cfg, host, players, lms_client=new_lms)

    # ⑤ 更新 panel 内部引用并恢复界面
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
    # 启动时按配置决定浮窗显隐（先 hide 保证窗口已创建但不干扰后续流程，
    # 连接成功后再根据 OVERLAY_VISIBLE 显式 show/hide）
    overlay.hide()

    # ① 优先用 LAST_IP 快速连接（不等自动发现）
    quick = _try_quick_connect(cfg)
    if quick:
        host, port, players = quick
    else:
        # ② 自动发现（带异常保护）
        print("[启动] 尝试自动发现服务器…")
        try:
            discovered = NetDiscovery.auto_discover()
        except Exception as e:
            print(f"[启动] 自动发现异常: {e}")
            discovered = None

        if not discovered:
            # ③ 弹出手动连接对话框
            print("[启动] 自动发现失败，弹出连接对话框")
            dlg = ConnectDialog(overlay.root, cfg)
            discovered = dlg.wait()
            if not discovered:
                print("[启动] 用户取消，退出")
                overlay.root.destroy()
                return
        host, port = discovered

        # 用发现的 IP 连接
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

    # 保存本次 IP 供下次快速连接
    cfg.LAST_IP = host
    try:
        cfg.save()
    except Exception:
        pass

    # ★ 先创建 lms，供 MAC 探测使用
    lms = LmsClient(host, port, cfg.PLAYER_MAC)

    # ★ MAC 记忆：优先走 LMS API（serverstatus），兜底 playerid
    _remember_host_mac(cfg, host, players, lms_client=lms)

    # 组装主模块
    fetcher = LyricsFetcher(lms)
    panel = ControlPanel(lms, overlay, fetcher, cfg)
    _apply_icon(panel.panel)
    panel.player_name = players[0][1]
    panel.lbl_server.config(text=f"服务器: {host}:{port}")
    
    # ★ 挂载关机回调：关机成功后弹出重连对话框
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

    # 按配置决定浮窗是否显示
    if getattr(cfg, "OVERLAY_VISIBLE", True):
        overlay.show()
    else:
        overlay.hide()

    overlay.run()


if __name__ == "__main__":
    main()