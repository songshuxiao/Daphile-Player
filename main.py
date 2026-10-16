# -*- coding: utf-8 -*-
"""程序入口：加载配置 → 全自动发现服务器 → 组装各模块 → 启动主循环"""

import os
import sys
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
    """兼容 PyInstaller：运行时资源在 _MEIPASS 中，开发时在源码目录"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _apply_icon(window):
    """给窗口设置图标（.ico 优先，失败静默）"""
    ico = _resource_path("icon.ico")
    if os.path.isfile(ico):
        try:
            window.iconbitmap(ico)
        except tk.TclError:
            pass


def main():
    cfg = Config()
    cfg.load()

    overlay = KaraokeOverlay(cfg)
    _apply_icon(overlay.root)        # ★ 浮窗图标
    overlay.hide()

    discovered = NetDiscovery.auto_discover()
    if not discovered:
        messagebox.showerror(
            "未找到服务器",
            "自动发现失败，请检查：\n\n"
            "1. Daphile/LMS 服务器与本机在同一网段且已开机\n"
            "2. 防火墙放行 UDP 3483 / TCP 9000")
        overlay.root.destroy()
        return

    host, port = discovered
    lms = LmsClient(host, port, cfg.PLAYER_MAC)

    try:
        players = lms.list_players()
        if not players:
            messagebox.showerror("错误", "服务器上未发现任何 player")
            overlay.root.destroy()
            return
        print("发现 players:", players)
    except Exception as e:
        messagebox.showerror("连接失败", f"无法连接 {host}:{port}\n{e}")
        overlay.root.destroy()
        return

    fetcher = LyricsFetcher(lms)
    panel = ControlPanel(lms, overlay, fetcher, cfg)
    _apply_icon(panel.panel)         # ★ 控制面板图标
    panel.player_name = players[0][1]
    panel.lbl_server.config(text=f"服务器: {host}:{port} (自动发现)")

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