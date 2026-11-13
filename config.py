# -*- coding: utf-8 -*-
"""配置项与持久化：
   · 用户设置保存到 user-config.json
   · 全部为实例方法：保证对实例属性的修改能被 save() 读到
   · 打包后写到 exe 同级目录（而非 PyInstaller 临时解压目录）"""

import os
import sys
import json


def _writable_base():
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if os.access(exe_dir, os.W_OK):
            return exe_dir
        fallback = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "LMSKaraoke")
        os.makedirs(fallback, exist_ok=True)
        return fallback
    return os.path.dirname(os.path.abspath(__file__))


_BASE_DIR = _writable_base()
USER_CONFIG_FILE = os.path.join(_BASE_DIR, "user-config.json")

print(f"[配置] 配置目录: {_BASE_DIR}")
print(f"[配置] 配置路径: {USER_CONFIG_FILE}")


class Config:
    # ---- 默认值 ----
    LMS_PORT = 9000
    PLAYER_MAC = ""
    POLL_INTERVAL = 0.2

    # 歌词浮窗位置：-1 表示首次启动自动计算（居中靠下，高于任务栏）
    OVERLAY_X, OVERLAY_Y = -1, -1
    # 高度加大到 160：30pt 主行 + 翻译行两行显示宽松，不重叠
    OVERLAY_W, OVERLAY_H = 980, 160
    
    # 主面板位置（-1 表示未设置，首次启动居中显示）
    PANEL_X, PANEL_Y = -1, -1
    # ★ 主面板尺寸 + 用户调整标志
    PANEL_W, PANEL_H = 500, 399
    PANEL_USER_RESIZED = False
    # ★ 播放 tab 独立高度（不受其他 tab 调整影响）
    PANEL_H_PLAY = 364           # 330 + TAB_BAR_H
    PLAY_TAB_RESIZED = False

    FONT_FAMILY = "Microsoft YaHei UI"
    FONT_SIZE = 30

    COLOR_ACTIVE = "#5453d7"
    COLOR_IDLE   = "#F0F0F8"
    COLOR_KEY    = "#010204"
    COLOR_STROKE = "#B8B8C4"
    OVERLAY_VISIBLE = True      # 桌面歌词浮窗是否显示（持久化）

    # ★ 新增：深色模式
    DARK_MODE = True

    SCAN_PORTS = (9000, 80)
    MAX_DOWNLOAD_MB = 20

    # 手动连接 / 远程唤醒
    HOST_MAC = ""    # Daphile 主机网卡 MAC，用于 WOL
    LAST_IP  = ""    # 上次成功/手动输入的 IP，便于下次预填
    # 更新检查
    AUTO_CHECK_UPDATE = True    # 启动时是否自动检查更新
    # —— 收藏 / 歌单 ——
    # 按设备主机 MAC 分组，切换设备时各自独立，换回原设备仍可用
    # FAVORITES = {"mac": [{"id", "title", "artist", "album"}, ...], ...}
    # PLAYLISTS = {"mac": [{"name", "tracks": [...]}, ...], ...}
    FAVORITES = {}
    PLAYLISTS = {}
    # ★ 封面 URL 缓存（按主机 MAC 分组，避免跨设备串号）
    #   ALBUM_COVER_URLS = {"host_mac": {"album_id": "url"}}
    #   TRACK_COVER_URLS = {"host_mac": {"c:<coverid>" 或 "t:<trackid>": "url"}}
    ALBUM_COVER_URLS = {}
    TRACK_COVER_URLS = {}

    # 键盘步进（可在设置面板调整）
    SEEK_STEP = 5    # 左右键 seek 步进（秒），默认 5
    VOL_STEP  = 2    # 上下键音量步进（%），默认 2

    _PERSIST_KEYS = ("COLOR_ACTIVE", "COLOR_IDLE", "COLOR_KEY", "COLOR_STROKE",
                     "FONT_FAMILY", "FONT_SIZE",
                     "OVERLAY_X", "OVERLAY_Y", "OVERLAY_W", "OVERLAY_H",
                     "PANEL_X", "PANEL_Y",
                     "PANEL_W", "PANEL_H", "PANEL_USER_RESIZED",
                     "PANEL_H_PLAY", "PLAY_TAB_RESIZED",
                     "OVERLAY_VISIBLE","DARK_MODE",
                     "HOST_MAC", "LAST_IP",
                     "AUTO_CHECK_UPDATE",
                     "SEEK_STEP", "VOL_STEP",
                     "FAVORITES", "PLAYLISTS")

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