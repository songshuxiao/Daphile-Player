# -*- coding: utf-8 -*-
"""配置项与持久化：
   · 用户设置保存到 user-config.json
   · 全部为实例方法：保证对实例属性的修改能被 save() 读到
   · 打包后写到 exe 同级目录（而非 PyInstaller 临时解压目录）"""

import os
import sys
import json


def _writable_base():
    """返回可写目录：
       · 开发时：源码目录
       · 打包后：exe 所在目录；若该目录不可写则退到 %APPDATA%\\LMSKaraoke"""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if os.access(exe_dir, os.W_OK):
            return exe_dir
        # exe 在 Program Files 之类不可写目录 → 回退到 APPDATA
        fallback = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "LMSKaraoke")
        os.makedirs(fallback, exist_ok=True)
        return fallback
    return os.path.dirname(os.path.abspath(__file__))


_BASE_DIR = _writable_base()
USER_CONFIG_FILE = os.path.join(_BASE_DIR, "user-config.json")

# ★ 启动时打印配置路径，方便诊断
print(f"[配置] 配置目录: {_BASE_DIR}")
print(f"[配置] 配置路径: {USER_CONFIG_FILE}")


class Config:
    # ---- 默认值 ----
    LMS_PORT = 9000
    PLAYER_MAC = ""
    POLL_INTERVAL = 0.2

    OVERLAY_X, OVERLAY_Y = 100, 620
    OVERLAY_W, OVERLAY_H = 980, 130
    FONT_FAMILY = "Microsoft YaHei UI"
    FONT_SIZE = 30

    COLOR_ACTIVE = "#03E8FB"
    COLOR_IDLE   = "#F0F0F8"
    COLOR_KEY    = "#010204"
    COLOR_STROKE = "#B8B8C4"

    SCAN_PORTS = (9000, 80)
    MAX_DOWNLOAD_MB = 20

    _PERSIST_KEYS = ("COLOR_ACTIVE", "COLOR_IDLE", "COLOR_KEY",
                     "COLOR_STROKE",  
                     "FONT_FAMILY", "FONT_SIZE",
                     "OVERLAY_X", "OVERLAY_Y", "OVERLAY_W", "OVERLAY_H")

    def __init__(self):
        for k in list(self._PERSIST_KEYS) + \
                ["LMS_PORT", "PLAYER_MAC", "POLL_INTERVAL",
                 "SCAN_PORTS", "MAX_DOWNLOAD_MB"]:
            setattr(self, k, getattr(type(self), k))

    def load(self):
        if os.path.isfile(USER_CONFIG_FILE):
            try:
                with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for key in self._PERSIST_KEYS:
                    if key in data:
                        setattr(self, key, data[key])
                print(f"[配置] 已加载用户配置: {USER_CONFIG_FILE}")
            except (OSError, ValueError) as e:
                print(f"[配置] 用户配置加载失败，使用默认值: {e}")
        else:
            print("[配置] 无 user-config.json，使用默认配置")
        self._sanitize_font()

    def _sanitize_font(self):
        fam = (self.FONT_FAMILY or "").strip()
        if fam.startswith("@") or fam.startswith("＠"):
            fam = fam.lstrip("@＠").strip()
        if not fam:
            fam = "Microsoft YaHei UI"
        self.FONT_FAMILY = fam

    def save(self):
        try:
            data = {k: getattr(self, k) for k in self._PERSIST_KEYS}
            with open(USER_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[配置] 已保存: {USER_CONFIG_FILE}")
        except OSError as e:
            print(f"[配置] 保存失败: {e}")