# -*- coding: utf-8 -*-
"""控制面板：正在播放 / 音乐库 / 歌词外观设置（深色/浅色主题切换）"""

import os
import sys
import json
import threading
import queue
import time
import traceback
import urllib.request
import webbrowser
import math  
import tkinter as tk
try:
    import ctypes
except ImportError:
    ctypes = None
from tkinter import ttk, colorchooser, messagebox, font as tkfont, simpledialog
from io import BytesIO

from lrc_parser import LrcParser

try:
    from PIL import Image, ImageDraw, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


APP_VERSION = "v8.0.0"
APP_REPO = "songshuxiao/Daphile-Player"
GITHUB_URL = f"https://github.com/{APP_REPO}"
RELEASES_API = f"https://api.github.com/repos/{APP_REPO}/releases/latest"
DOWNLOAD_URL = "https://cloud.szfx.top/Windows/235.html"


def _resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)

def apply_dark_titlebar(window, dark=True, bg_color=None):
    """Windows 10 build 17763+ / Windows 11：把 Toplevel 的标题栏设为深色。
       dark=True 且 bg_color="#RRGGBB" 时，Win11 22000+ 会自定义标题栏颜色；
       浅色模式下恢复系统默认标题栏。非 Windows 平台无副作用。"""
    if os.name != "nt" or ctypes is None:
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            hwnd = window.winfo_id()
        if not hwnd:
            return

        # 20 = Win10 18985+ / Win11 新属性；19 = Win10 17763~18984 旧属性
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            try:
                hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
                if hr == 0:
                    break
            except Exception:
                continue

        if dark and bg_color:
            try:
                h = bg_color.lstrip("#")
                if len(h) == 6:
                    r = int(h[0:2], 16)
                    g = int(h[2:4], 16)
                    b = int(h[4:6], 16)
                    # COLORREF = 0x00BBGGRR
                    colorref = ctypes.c_uint(r | (g << 8) | (b << 16))
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, 35, ctypes.byref(colorref),
                        ctypes.sizeof(colorref))
                    # 浅色文字（暗背景上可读）
                    txt = ctypes.c_uint(0xF0 | (0xF0 << 8) | (0xF8 << 16))
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, 36, ctypes.byref(txt),
                        ctypes.sizeof(txt))
            except Exception:
                pass
        else:
            # 浅色模式：恢复系统默认标题栏
            try:
                default = ctypes.c_uint(0xFFFFFFFF)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 35, ctypes.byref(default),
                    ctypes.sizeof(default))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 36, ctypes.byref(default),
                    ctypes.sizeof(default))
            except Exception:
                pass
    except Exception:
        pass

class CanvasSlider(tk.Canvas):
    """圆角、可拖动的水平滑块，兼容 ttk.Scale 的常用接口"""

    def __init__(self, parent, from_=0.0, to=100.0, value=0.0,
                 command=None,
                 bg="#15151E", trough="#0F0F16",
                 fill="#5A5AE0", thumb="#E8E8F0",
                 thumb_hover="#FFFFFF",
                 height=22, track_height=6, thumb_radius=8,
                 on_drag_start=None, on_drag_end=None,
                 **kwargs):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0,
                         height=height, **kwargs)
        self._from = float(from_)
        self._to = float(to)
        self._value = float(value)
        self._command = command
        self._on_drag_start = on_drag_start
        self._on_drag_end = on_drag_end
        self._trough = trough
        self._fill = fill
        self._thumb = thumb
        self._thumb_hover = thumb_hover
        self._track_h = track_height
        self._thumb_r = thumb_radius
        self._canvas_h = height
        self._dragging = False
        self._hover = False

        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.config(cursor="hand2")

    def get(self):
        return self._value

    def set(self, v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return
        v = max(self._from, min(self._to, v))
        if abs(v - self._value) < 1e-6:
            return
        self._value = v
        self._redraw()

    def config(self, *args, **kwargs):
        if "to" in kwargs:
            new_to = kwargs.pop("to")
            try:
                self._to = float(new_to)
            except (TypeError, ValueError):
                pass
            if self._value > self._to:
                self._value = self._to
            self._redraw()
        if "from_" in kwargs:
            try:
                self._from = float(kwargs.pop("from_"))
            except (TypeError, ValueError):
                pass
        if args or kwargs:
            return super().config(*args, **kwargs)

    configure = config

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width()
        if w < 10:
            return
        h = self._canvas_h
        cy = h // 2
        r = self._thumb_r
        th = self._track_h

        x0 = r + 2
        x1 = w - r - 2
        if x1 <= x0:
            return

        span = self._to - self._from
        ratio = 0.0 if span <= 0 else (self._value - self._from) / span
        ratio = max(0.0, min(1.0, ratio))
        thumb_x = x0 + ratio * (x1 - x0)

        self._round_rect(x0, cy - th // 2, x1, cy + th // 2,
                         radius=th // 2, fill=self._trough)
        if thumb_x > x0 + 1:
            self._round_rect(x0, cy - th // 2, thumb_x, cy + th // 2,
                             radius=th // 2, fill=self._fill)

        tc = self._thumb_hover if (self._hover or self._dragging) else self._thumb
        self.create_oval(thumb_x - r, cy - r, thumb_x + r, cy + r,
                         fill=tc, outline="")

    def _round_rect(self, x1, y1, x2, y2, radius=0, **kwargs):
        if radius <= 0:
            self.create_rectangle(x1, y1, x2, y2, **kwargs)
            return
        r = radius
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        self.create_polygon(points, smooth=True, **kwargs)

    def _set_from_x(self, x):
        r = self._thumb_r
        w = self.winfo_width()
        x0 = r + 2
        x1 = w - r - 2
        if x1 <= x0:
            return
        ratio = (x - x0) / (x1 - x0)
        ratio = max(0.0, min(1.0, ratio))
        v = self._from + ratio * (self._to - self._from)
        if abs(v - self._value) < 1e-6:
            return
        self._value = v
        self._redraw()
        if self._command:
            try:
                self._command(str(v))
            except Exception:
                pass

    def _on_press(self, e):
        self._dragging = True
        if callable(self._on_drag_start):
            self._on_drag_start()
        self._set_from_x(e.x)

    def _on_drag(self, e):
        if self._dragging:
            self._set_from_x(e.x)

    def _on_release(self, e):
        was_dragging = self._dragging
        self._dragging = False
        self._redraw()
        if was_dragging and callable(self._on_drag_end):
            self._on_drag_end()

    def _on_enter(self, e):
        self._hover = True
        self._redraw()

    def _on_leave(self, e):
        self._hover = False
        self._redraw()

    def _update_theme(self, panel):
        self.configure(bg=panel.BG)
        self._trough = "#E0E0E8" if not panel.cfg.DARK_MODE else "#0F0F16"
        self._fill = panel.ACCENT
        self._thumb = panel.ACCENT
        self._thumb_hover = "#7A7AFF"
        self._redraw()


class ControlPanel:

    def __init__(self, lms, overlay, fetcher, cfg):
        self.lms = lms
        self.overlay = overlay
        self.fetcher = fetcher
        self.cfg = cfg

        if getattr(self.cfg, "DARK_MODE", True):
            self.BG = "#15151E"; self.CARD = "#20202E"; self.FIELD = "#1A1A24"
            self.FG = "#E8E8F0"; self.DIM = "#9A9AB0"; self.SEL_BG = "#3D3D5C"
            self.ACCENT = "#5A5AE0"
            self.HOVER_BG = "#3D3D5C"
            self.HOVER_ACCENT = "#7A7AFF"
        else:
            self.BG = "#F0F2F5"; self.CARD = "#FFFFFF"; self.FIELD = "#FFFFFF"
            self.FG = "#1A1A24"; self.DIM = "#5A5A6A"; self.SEL_BG = "#E0E0E8"
            self.ACCENT = "#4A4AE0"
            self.HOVER_BG = "#E0E0E8"
            self.HOVER_ACCENT = "#7A7AFF"

        self.track_info = {}
        self.song_pos = 0.0
        self._duration = 0.0
        self._seeking = False
        self.lines = []
        self.cur_line_idx = -1
        self._last_idx = -1
        self._lyrics_source = ""
        self._fetch_token = 0
        self._playing = False
        self.player_name = ""
        self._vol_dragging = False
        self._last_overlay_vis = None
        self._last_play_press = 0
        self._last_seek_ts = 0.0        # ★ 最近一次 seek 的时间戳

        self._available_players = []
        self._last_players_check = 0.0
        self._known_player_macs = set()

        self._cover_cache = {}
        self._current_cover_id = ""
        self._cover_photo = None
        self._icon_cache = {}

        self.on_daphile_shutdown = None

        self._lib_artists = {}
        self._lib_albums = {}
        self._lib_tracks = {}

        self._lib_artist_token = 0
        self._lib_album_token = 0
        self._lib_track_token = 0
       
        # —— 新歌曲 Tab ——
        self._ns_albums = []
        self._ns_tracks = {}
        self._ns_loaded = False
        self._ns_view = "grid"          # "grid" / "list"
        self._ns_current_album = None   # (album_id, album_name, artist, coverid)
        self._ns_cover_cache = {}       # 网格封面缓存
        self._ns_album_cover_cache = {} # 底部大图缓存
        self._ns_cover_size = 132
        self._ns_cover_queue = queue.Queue()
        self._ns_first_track_cache = {}   # album_id -> (coverid, track_id)
        self._ns_grid_panel_h = None      # 网格模式下面板高度缓存        
        self._ns_last_synced_track = None   # 上次同步到 Treeview 的 track_id
        self._lib_last_synced_track = None   # 音乐库：上次同步到 Treeview 的 track_id
        # —— 收藏 / 歌单 ——
        self._fav_tracks = {}   # iid -> (track_id, title, artist, album)

        self._fav_last_synced_track = None   # 收藏页：上次同步到 Treeview 的 track_id
        self._fav_pending_track = None       # 收藏页：等待 poll 确认的 track_id

        self._pl_tracks = {}    # iid -> (track_id, title, artist, album, index)
        self._heart_cache = {}
        # 切歌模式："smart" = 智能优选（同专辑/同歌手/队列）
        #          "queue" = 严格按队列顺序（收藏列表等）
        self._play_mode = "smart"

        # —— 简洁模式 ——
        self._compact_mode = False
        self._compact_saved = {}
        self._compact_root = None
        self._compact_card = None
        self._compact_cover = None
        self._compact_title = None
        self._compact_artist = None
        self._compact_ctl = None
        self._compact_prev = None
        self._compact_play = None
        self._compact_next = None
        self._compact_restore = None
        
        # 键盘导航标志位
        self._kb_focus_albums = False
        self._kb_select_tracks = False

        self._update_checking = False
        self._about_status_lbl = None
        self._about_auto_toggle = None
        self._tooltip_win = None        # 当前悬浮气泡 Toplevel

        self.panel = tk.Toplevel(overlay.root)
        self.panel.title("达菲播放器")
        self.panel.geometry("500x365")
        self.panel.configure(bg=self.BG)

        self._build_style()
        self._build_ui()
        self._sync_font_controls()

        self.panel.update_idletasks()
        try:
            h = self._calc_panel_height()
            self._place_panel_initial(h)
        except tk.TclError:
            pass

        # 绑定键盘全局事件
        self.panel.bind("<KeyPress>", self._on_key_press)
        # 滚轮控制音量（播放中 / 简洁模式生效）
        self.panel.bind("<MouseWheel>", self._on_mousewheel_volume, add="+")        
        # ★ 点击面板上任意非输入控件时，主动把焦点从 Spinbox/Entry 移开
        self.panel.bind("<Button-1>", self._on_panel_click, add="+")

        self._stop = False
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self._ui_tick_loop()

        if getattr(self.cfg, "AUTO_CHECK_UPDATE", True):
            self.panel.after(3000, lambda: self._check_update(auto=True))
        # 标题栏深色（窗口真正映射后再设置更可靠）
        try:
            self.panel.after(150, self._apply_titlebar_theme)
        except tk.TclError:
            pass
        # 启动时尝试把旧的 list 收藏/歌单迁到当前设备 MAC 下
        try:
            self._migrate_legacy_fav_pl()
        except Exception:
            traceback.print_exc()

    def _on_mousewheel_volume(self, event):
        """滚轮控制音量，幅度与键盘上下键一致（VOL_STEP）。
           仅「播放中」标签页与简洁模式生效；输入类控件保留自身滚轮行为。"""
        try:
            delta = event.delta
        except AttributeError:
            return None
        if not delta:
            return None

        if self._compact_mode:
            # 简洁模式：无其他可滚动控件，直接处理
            pass
        else:
            # 完整模式：仅「播放中」标签页生效
            try:
                if self.nb.index(self.nb.select()) != 0:
                    return None
            except (tk.TclError, ValueError):
                return None
            # Spinbox / Entry / Combobox / Text 保留自身滚轮行为
            if self._is_input_widget(event.widget):
                return None

        # 向上滚 = 音量 +step；向下滚 = 音量 -step
        steps = int(delta / 120)
        if steps == 0:
            steps = 1 if delta > 0 else -1

        step = max(1, int(getattr(self.cfg, "VOL_STEP", 5)))
        try:
            cur_vol = int(self.scl_vol.get())
        except (TypeError, ValueError, tk.TclError):
            cur_vol = 50
        new_vol = max(0, min(100, cur_vol + steps * step))
        if new_vol == cur_vol:
            return "break"

        self.scl_vol.set(new_vol)
        self._on_volume(str(new_vol))
        return "break"
            
    # ================= 键盘快捷键 =================
    def _on_key_press(self, event):
        w = event.widget
        # 如果焦点在输入框/微调框/下拉框里，不拦截，让用户可以正常打字
        if isinstance(w, (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Combobox, tk.Text)):
            return None

        # Tab 循环切换 播放中 → 音乐库 → 新歌曲 → 播放中
        if event.keysym == "Tab":
            if self._compact_mode:
                return "break"
            cur = self.nb.index(self.nb.select())
            new_tab = (cur + 1) % 5
            self.nb.select(new_tab)
            if new_tab == 1:
                try:
                    self.panel.update_idletasks()
                    self._focus_library_tree()
                except tk.TclError:
                    pass
            elif new_tab == 2:
                try:
                    self._ns_ensure_loaded()
                    self.f3.focus_set()
                except tk.TclError:
                    pass
            elif new_tab == 3:
                try:
                    self._fav_ensure_loaded()
                    self.fav_tree.focus_set()
                except tk.TclError:
                    pass
            elif new_tab == 4:
                try:
                    self._pl_ensure_loaded()
                    self.pl_tree.focus_set()
                except tk.TclError:
                    pass
            else:
                try:
                    self.panel.focus_set()
                except tk.TclError:
                    pass
            return "break"

        try:
            cur_tab = self.nb.index(self.nb.select())
        except (tk.TclError, ValueError):
            return None

        if cur_tab == 0:
            # ---- 播放中标签页：左/右 seek，上/下音量，空格暂停 ----
            if event.keysym == "space":
                self._toggle_play()
                return "break"
            elif event.keysym == "Up":
                step = max(1, int(getattr(self.cfg, "VOL_STEP", 5)))
                vol = min(100, int(self.scl_vol.get() + step))
                self.scl_vol.set(vol)
                self._on_volume(str(vol))
                return "break"
            elif event.keysym == "Down":
                step = max(1, int(getattr(self.cfg, "VOL_STEP", 5)))
                vol = max(0, int(self.scl_vol.get() - step))
                self.scl_vol.set(vol)
                self._on_volume(str(vol))
                return "break"
            elif event.keysym == "Left":
                step = max(1, int(getattr(self.cfg, "SEEK_STEP", 5)))
                pos = max(0, self.song_pos - step)
                self.progress.set(pos)
                self.song_pos = pos
                self._last_seek_ts = time.time()

                def work_l(p=pos):
                    try:
                        self.lms.seek(p)
                    except Exception:
                        traceback.print_exc()
                threading.Thread(target=work_l, daemon=True).start()
                return "break"
            elif event.keysym == "Right":
                step = max(1, int(getattr(self.cfg, "SEEK_STEP", 5)))
                pos = min(self._duration, self.song_pos + step) if self._duration else self.song_pos + step
                self.progress.set(pos)
                self.song_pos = pos
                self._last_seek_ts = time.time()

                def work_r(p=pos):
                    try:
                        self.lms.seek(p)
                    except Exception:
                        traceback.print_exc()
                threading.Thread(target=work_r, daemon=True).start()
                return "break"
        else:
            # ---- 音乐库标签页：拦截方向键，防止 ttk.Notebook 默认绑定切换标签 ----
            # 焦点在树控件上时由各 tree 的 widget bind 先行处理，不会走到这里
            if event.keysym in ("Left", "Right"):
                return "break"
        return None

    # ---- 焦点管理：避免输入框吞掉上下左右键 ----
    @staticmethod
    def _is_input_widget(w):
        """判断是否为输入类控件（其内部按键不应被全局拦截）"""
        if w is None:
            return False
        try:
            return isinstance(w, (tk.Entry, ttk.Entry, tk.Spinbox,
                                  getattr(ttk, "Spinbox", tk.Spinbox),
                                  ttk.Combobox, tk.Text))
        except (AttributeError, tk.TclError):
            return False

    def _commit_spinbox(self, _e=None):
        """Spinbox 内按 Enter / ESC：提交当前输入并交还焦点给 panel，
           之后上下左右键可正常调进度/音量"""
        try:
            self._on_seek_step_change()
            self._on_vol_step_change()
        except Exception:
            pass
        try:
            self.panel.focus_set()
        except tk.TclError:
            pass
        return "break"

    def _on_panel_click(self, event):
        """点击面板空白处（非输入控件）时把焦点从 Spinbox/Entry 移开"""
        w = event.widget
        if self._is_input_widget(w):
            return None
        try:
            cur = self.panel.focus_get()
        except (tk.TclError, KeyError):
            cur = None
        if self._is_input_widget(cur):
            # 失焦前先提交输入
            try:
                self._on_seek_step_change()
                self._on_vol_step_change()
            except Exception:
                pass
            try:
                self.panel.focus_set()
            except tk.TclError:
                pass
        return None

    def _in_library(self):
        """当前是否在音乐库标签页"""
        try:
            return self.nb.index(self.nb.select()) == 1
        except (tk.TclError, ValueError, AttributeError):
            return False

    def _focus_library_tree(self):
        """Tab 切回音乐库：把焦点还给用户最后操作的那一层
           优先级：歌曲（有选中） > 专辑（有选中） > 歌手（默认）"""
        try:
            if self.tree_tracks.selection():
                self.tree_tracks.focus_set()
                return
            if self.tree_albums.selection():
                self.tree_albums.focus_set()
                return
            self.tree_artists.focus_set()
        except tk.TclError:
            pass
        
    def _kb_artist_left(self, event):
        """歌手列按左：音乐库标签页内无操作；不在音乐库时交给全局键盘处理"""
        if not self._in_library():
            return self._on_key_press(event)
        return "break"  # 歌手列左键无操作，不取消高亮

    def _kb_artist_down(self, event):
        """歌手列按下：若尚无选中项，则选中第一个歌手；否则交给默认下移行为"""
        if not self._in_library():
            return self._on_key_press(event)
        children = self.tree_artists.get_children()
        if not children:
            return "break"
        if not self.tree_artists.selection():
            self.tree_artists.selection_set(children[0])
            self.tree_artists.focus(children[0])
            return "break"
        # 已有选中：不拦截，让 Treeview 默认行为移动到下一项
        return None
    
    def _kb_artist_right(self, event):
        """歌手列按右/回车：选中歌手 -> 展开专辑并高亮第一张 -> 展开歌曲(不选中)"""
        if not self._in_library():
            return self._on_key_press(event)
        children = self.tree_artists.get_children()
        if not children:
            return "break"
            
        sel = self.tree_artists.selection()
        if not sel:
            self.tree_artists.selection_set(children[0])
            self.tree_artists.focus(children[0])
            self._on_artist_selected()
            
        self._set_headers("albums")
        self.tree_albums.focus_set()
        
        album_children = self.tree_albums.get_children()
        if album_children:
            self.tree_albums.selection_set(album_children[0])
            self.tree_albums.focus(album_children[0])
            self._kb_select_tracks = False
            self._on_album_selected()
        else:
            self._kb_focus_albums = True
            
        return "break"

    def _kb_album_right(self, event):
        """专辑列按右/回车：选中专辑 -> 展开歌曲并高亮第一首"""
        if not self._in_library():
            return self._on_key_press(event)
        children = self.tree_albums.get_children()
        if not children:
            return "break"
            
        sel = self.tree_albums.selection()
        if not sel:
            self.tree_albums.selection_set(children[0])
            self.tree_albums.focus(children[0])
            self._on_album_selected()
            
        self._set_headers("tracks")
        self.tree_tracks.focus_set()
        
        track_children = self.tree_tracks.get_children()
        if track_children:
            self.tree_tracks.selection_set(track_children[0])
            self.tree_tracks.focus(track_children[0])
        else:
            self._kb_select_tracks = True
            
        return "break"

    def _kb_track_left(self, event):
        """歌曲列按左：只取消歌曲高亮，焦点回到专辑列。
           ★ 保留歌曲/专辑列展开；保留专辑原有高亮（若无选中则自动选中第一张）。"""
        if not self._in_library():
            return self._on_key_press(event)
        self.tree_tracks.selection_remove(self.tree_tracks.selection())
        if not self.tree_albums.selection():
            album_children = self.tree_albums.get_children()
            if album_children:
                self.tree_albums.selection_set(album_children[0])
                self.tree_albums.focus(album_children[0])
        else:
            self.tree_albums.focus(self.tree_albums.selection()[0])
        self.tree_albums.focus_set()
        return "break"

    def _kb_album_left(self, event):
        """专辑列按左：取消专辑+歌曲高亮，焦点回到歌手列。
           ★ 保留专辑/歌曲列展开；保留歌手原有高亮（若无选中则自动选中第一位）。"""
        if not self._in_library():
            return self._on_key_press(event)
        self.tree_albums.selection_remove(self.tree_albums.selection())
        self.tree_tracks.selection_remove(self.tree_tracks.selection())
        if not self.tree_artists.selection():
            artist_children = self.tree_artists.get_children()
            if artist_children:
                self.tree_artists.selection_set(artist_children[0])
                self.tree_artists.focus(artist_children[0])
        else:
            self.tree_artists.focus(self.tree_artists.selection()[0])
        self.tree_artists.focus_set()
        return "break"

    def _kb_escape(self, event):
        """ESC：收起所有下级列，清空专辑/歌曲高亮，焦点与高亮回到歌手列"""
        if not self._in_library():
            return "break"  # 播放中标签页按 ESC 无操作
        self.tree_albums.selection_remove(self.tree_albums.selection())
        self.tree_tracks.selection_remove(self.tree_tracks.selection())
        self._set_headers("artists")
        if not self.tree_artists.selection():
            artist_children = self.tree_artists.get_children()
            if artist_children:
                self.tree_artists.selection_set(artist_children[0])
                self.tree_artists.focus(artist_children[0])
        else:
            self.tree_artists.focus(self.tree_artists.selection()[0])
        self.tree_artists.focus_set()
        return "break"

    def _kb_track_right(self, event):
        """歌曲列按右：不播放，防止误触"""
        if not self._in_library():
            return self._on_key_press(event)
        return "break"

    def _kb_track_play(self, event):
        """歌曲列按回车：播放当前歌曲"""
        if not self._in_library():
            return self._on_key_press(event)
        children = self.tree_tracks.get_children()
        if not children:
            return "break"
            
        sel = self.tree_tracks.selection()
        if not sel:
            self.tree_tracks.selection_set(children[0])
            self.tree_tracks.focus(children[0])
            sel = self.tree_tracks.selection()
            
        if sel:
            info = self._lib_tracks.get(sel[0])
            if info:
                tid, tname = info
                self.lms.play_track(tid)
                self._lib_set_status(f"正在播放: {tname}")
        return "break"

    # ================= 样式 =================
    def _build_style(self):
        style = ttk.Style(self.panel)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        
        style.layout("TNotebook.Tab", [
            ("Notebook.tab", {"sticky": "nswe", "children": [
                ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                    ("Notebook.label", {"side": "top", "sticky": ""})
                ]})
            ]})
        ])

        style.configure(".", background=self.BG, foreground=self.FG,
                        fieldbackground=self.FIELD, borderwidth=0)
        style.configure("TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.CARD)
        style.configure("TLabel", background=self.BG, foreground=self.FG)
        style.configure("Card.TLabel", background=self.CARD, foreground=self.FG)
        style.configure("Dim.TLabel", background=self.BG, foreground=self.DIM)

        style.configure("TButton", background=self.CARD, foreground=self.FG,
                        padding=(8, 4))
        style.map("TButton",
                  background=[("active", self.SEL_BG), ("pressed", self.ACCENT)],
                  foreground=[("active", self.FG)])

        style.configure("TNotebook", background=self.BG, borderwidth=0)

        # ★ tab 边框色：浅色模式下选中 tab 背景 SEL_BG=#E0E0E8 与背景 BG=#F0F2F5
        #   非常接近，clam 默认边框色更浅，导致竖线不可见。
        #   这里显式给出有明显对比的边框色，让分界清晰。
        if self.cfg.DARK_MODE:
            nb_light = "#5A5A78"
            nb_dark = "#2A2A40"
            nb_border = "#5A5A78"
        else:
            nb_light = "#FFFFFF"
            nb_dark = "#A8A8B8"
            nb_border = "#A8A8B8"

        style.configure("TNotebook.Tab",
                        background=self.CARD, foreground=self.FG,
                        padding=(15, 6), font=("Microsoft YaHei UI", 10),
                        lightcolor=nb_light,
                        darkcolor=nb_dark,
                        bordercolor=nb_border)
        style.map("TNotebook.Tab",
                  background=[("selected", self.SEL_BG)],
                  foreground=[("selected", "#FFFFFF" if self.cfg.DARK_MODE else "#1A1A24")],
                  lightcolor=[("selected", nb_light)],
                  darkcolor=[("selected", nb_dark)],
                  bordercolor=[("selected", nb_border)],
                  padding=[("selected", (17, 8)), ("!selected", (15, 6))],
                  expand=[("selected", [1, 0, 1, 0])])

        style.configure("TCombobox", fieldbackground=self.FIELD,
                        foreground=self.FG, background=self.CARD,
                        arrowcolor=self.FG)
        style.map("TCombobox",
                  fieldbackground=[("readonly", self.FIELD),
                                   ("active", self.FIELD)],
                  foreground=[("readonly", self.FG)],
                  selectbackground=[("readonly", self.FIELD)],
                  selectforeground=[("readonly", self.FG)])

        style.configure("TEntry", fieldbackground=self.FIELD,
                        foreground=self.FG, insertcolor=self.FG)
        style.map("TEntry",
                  fieldbackground=[("disabled", self.FIELD),
                                   ("readonly", self.FIELD),
                                   ("focus", self.FIELD),
                                   ("active", self.FIELD)],
                  foreground=[("disabled", self.FG), ("readonly", self.FG),
                              ("focus", self.FG)])

        style.configure("Library.Treeview",
                        background=self.FIELD, foreground=self.FG,
                        fieldbackground=self.FIELD, rowheight=26,
                        borderwidth=0, relief="flat")

        # ★ 加深选中背景色：深色模式用紫蓝强调色，浅色模式用深蓝强调色，文字纯白
        # ★ 新增 !focus selected：即使焦点离开某棵树，该树内已选中项依然保持高亮
        _sel_bg = "#5A5AE0" if self.cfg.DARK_MODE else "#4A4AE0"
        style.map("Library.Treeview",
                  background=[("selected", _sel_bg),
                              ("!focus selected", _sel_bg)],
                  foreground=[("selected", "#FFFFFF"),
                              ("!focus selected", "#FFFFFF")])

        style.layout("Library.Treeview", [
            ("Library.Treeview.treearea", {"sticky": "nswe"})
        ])
        style.configure("Library.Treeview.Heading", background=self.CARD,
                        foreground=self.DIM, relief="flat", borderwidth=0)
        # 新歌曲页滚动条：细、无槽边、颜色随主题
        style.configure("NewSongs.Vertical.TScrollbar",
                        gripcount=0,
                        background=self.SEL_BG,
                        darkcolor=self.SEL_BG,
                        lightcolor=self.SEL_BG,
                        troughcolor=self.BG,
                        bordercolor=self.BG,
                        arrowcolor=self.DIM,
                        width=10)
        style.map("NewSongs.Vertical.TScrollbar",
                  background=[("active", self.ACCENT),
                              ("pressed", self.ACCENT)],
                  arrowcolor=[("active", self.FG)])        

    # ================= UI 构建 =================
    def _build_ui(self):
        p = self.panel
        p.option_add("*TCombobox*Listbox.background", self.FIELD)
        p.option_add("*TCombobox*Listbox.foreground", self.FG)
        p.option_add("*TCombobox*Listbox.selectBackground", self.SEL_BG)
        p.option_add("*TCombobox*Listbox.selectForeground", self.FG)

        head = tk.Frame(p, bg=self.BG)
        head.pack(fill="x", padx=10, pady=(10, 4))
        self.head = head                       # ★ 新增

        # ① 先 pack 服务器图标按钮 → 占据最左位置
        self.btn_daphile_settings = self._make_server_button(
            head, self._open_daphile_settings, size=22)
        self.btn_daphile_settings.pack(side="left", padx=(0, 2))

        # ② 再 pack IP 文字 → 位于图标按钮右侧（已去掉 "服务器: " 前缀）
        self.lbl_server = tk.Label(head, text="搜索中…", bg=self.BG,
                                   fg=self.ACCENT,
                                   font=("Microsoft YaHei UI", 9),
                                   cursor="hand2")
        self.lbl_server.pack(side="left")
        self.lbl_server.bind("<Button-1>", self._open_lms_web)

        # 图标 + 名称 + 下拉箭头（去掉"播放器："文字前缀）
        speaker_photo = self._get_speaker_photo(14)
        self.lbl_player = tk.Label(
            head, text=" … ▼",
            image=speaker_photo,
            compound="left",
            bg=self.BG, fg=self.DIM,
            font=("Microsoft YaHei UI", 9), cursor="hand2")
        if speaker_photo is not None:
            self.lbl_player.image = speaker_photo   # 防止 GC
        self.lbl_player.pack(side="right")
        self.lbl_player.bind("<Button-1>", self._show_players_menu)

        # ★ ⏻达菲 
        self.btn_shutdown = self._make_round_btn(
            head, "⏻", self._shutdown_daphile,
            width=22, height=22, radius=8)
        self.btn_shutdown.pack(side="right", padx=(0, 6))

        nb = ttk.Notebook(p)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.nb = nb
        # ★ 在 widget 层提前拦截方向键，避免被 ttk.Notebook 的 class bind 抢先切标签
        nb.bind("<Left>", self._on_key_press)
        nb.bind("<Right>", self._on_key_press)

        btn_group = tk.Frame(nb, bg=self.BG)
        btn_group.place(relx=1.0, x=-8, y=10, anchor="ne")
        self._btn_group_widget = btn_group     # ★ 新增

        self.btn_settings = self._make_round_btn(
            btn_group, "⚙", self._toggle_lyrics_panel,
            width=22, height=22, radius=8)
        self.btn_settings.pack(side="left", padx=(0, 6))

        self.btn_lyrics = self._make_round_btn(
            btn_group, "♩", self._toggle_lyrics,
            width=22, height=22, radius=8)
        self.btn_lyrics.pack(side="left", padx=(0, 6))

        # ★ 简洁按钮：📌 emoji 视觉偏大，字号缩到 7 与 ⌕ / ♩ 视觉等高
        self.btn_compact = self._make_round_btn(
            btn_group, "📌", self._toggle_compact_mode,
            width=22, height=22, radius=8, font_size=7)
        self.btn_compact.pack(side="left")
        # ★ 悬浮提示（气泡样式）
        self._bind_tooltip(self.btn_settings, "歌词外观设置")
        self._bind_tooltip(
            self.btn_lyrics,
            lambda: "隐藏桌面歌词" if self.overlay.is_visible() else "显示桌面歌词")
        self._bind_tooltip(self.btn_compact, "简洁模式")
        # ★ 新增：达菲设置 / 关闭达菲
        self._bind_tooltip(self.btn_daphile_settings, "达菲设置")
        self._bind_tooltip(self.btn_shutdown, "关闭达菲")

        # ================= TAB 1：正在播放 =================
        f1 = ttk.Frame(nb, padding=10)
        nb.add(f1, text="播放")
        self._f1 = f1                          # ★ 新增

        card = tk.Frame(f1, bg=self.CARD)
        card.pack(fill="x", pady=(0, 8))
        self._card = card                      # ★ 新增

        self.cover_canvas = tk.Canvas(card, width=72, height=72,
                                      bg=self.CARD, highlightthickness=0, bd=0)
        self.cover_canvas.pack(side="right", padx=(0, 12), pady=10)

        info = tk.Frame(card, bg=self.CARD)
        info.pack(side="left", fill="both", expand=True, padx=12, pady=10)
        self._info_widget = info

        self.lbl_title = tk.Label(info, text="—", bg=self.CARD, fg=self.FG,
                                  font=("Microsoft YaHei UI", 14, "bold"),
                                  anchor="w", justify="left", wraplength=320)
        self.lbl_title.pack(fill="x", anchor="w")
        self.lbl_artist = tk.Label(info, text="", bg=self.CARD, fg=self.DIM,
                                   font=("Microsoft YaHei UI", 10), anchor="w",
                                   justify="left", wraplength=320)
        self.lbl_artist.pack(fill="x", anchor="w", pady=(2, 0))

        self._draw_default_cover()

        self.lbl_time = tk.Label(f1, text="00:00 / 00:00", bg=self.BG,
                                 fg=self.DIM, font=("Consolas", 10))
        self.lbl_time.pack(anchor="w")

        self.progress = CanvasSlider(
            f1, from_=0, to=100, value=0,
            bg=self.BG, trough="#0F0F16" if self.cfg.DARK_MODE else "#E0E0E8",
            fill=self.ACCENT, thumb=self.ACCENT, thumb_hover="#7A7AFF",
            height=22, track_height=6, thumb_radius=8,
            on_drag_start=self._seek_start, on_drag_end=self._seek_end)
        self.progress.pack(fill="x", pady=(4, 0))

        ctl = tk.Frame(f1, bg=self.BG)
        ctl.pack(pady=(10, 6))
        self._ctl = ctl                        # ★ 新增

        self.btn_prev = self._make_play_btn(
            ctl, "◀◀", lambda: self._cmd("prev"),
            diameter=38, font_size=10, primary=False)
        self.btn_prev.pack(side="left", padx=8)

        self.btn_play = self._make_play_btn(
            ctl, "▶", self._toggle_play,
            diameter=54, font_size=18, primary=True)
        self.btn_play.pack(side="left", padx=8)

        self.btn_next = self._make_play_btn(
            ctl, "▶▶", lambda: self._cmd("next"),
            diameter=38, font_size=10, primary=False)
        self.btn_next.pack(side="left", padx=8)

        # ★ 音量控制行：喇叭图标 + 百分比 + 滑块
        vol_row = tk.Frame(f1, bg=self.BG)
        vol_row.pack(fill="x", pady=(0, 10))
        self._vol_row = vol_row                # ★ 新增
        tk.Label(vol_row, text="🔊", bg=self.BG, fg=self.DIM,
                 font=("Segoe UI Emoji", 12)).pack(side="left")
        self.lbl_vol_pct = tk.Label(
            vol_row, text="50%", bg=self.BG, fg=self.DIM,
            font=("Consolas", 9), width=4, anchor="w")
        self.lbl_vol_pct.pack(side="left", padx=(4, 2))
        self.scl_vol = CanvasSlider(
            vol_row, from_=0, to=100, value=50,
            bg=self.BG, trough="#0F0F16" if self.cfg.DARK_MODE else "#E0E0E8",
            fill=self.ACCENT, thumb=self.ACCENT, thumb_hover="#7A7AFF",
            height=22, track_height=6, thumb_radius=8,
            command=self._on_volume,
            on_drag_start=self._on_vol_drag_start,
            on_drag_end=self._on_vol_drag_end)
        self.scl_vol.pack(side="left", fill="x", expand=True, padx=(0, 8))

        # ---- 歌词外观（默认隐藏） ----
        self.ap_container = tk.Frame(f1, bg=self.BG)

        self.ap = tk.LabelFrame(self.ap_container, text=" 歌词外观 ",
                                bg=self.CARD, fg=self.FG)
        self.ap.pack(fill="x")
        self._lyrics_appearance_visible = False

        r0 = tk.Frame(self.ap, bg=self.CARD)
        r0.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(r0, text="字体", bg=self.CARD, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.cmb_font = ttk.Combobox(r0, width=18, state="readonly",
                                     values=self._font_families())
        self.cmb_font.pack(side="left", padx=(4, 16))
        tk.Label(r0, text="字号", bg=self.CARD, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.spn_size = tk.Spinbox(r0, from_=12, to=72, width=5,
                                   bg=self.FIELD, fg=self.FG, relief="flat",
                                   buttonbackground=self.CARD,
                                   insertbackground=self.FG)
        self.spn_size.pack(side="left", padx=4)

        r1 = tk.Frame(self.ap, bg=self.CARD)
        r1.pack(fill="x", padx=10, pady=(6, 10))
        for col in range(3):
            r1.columnconfigure(col, weight=1)

        frame_a, self._swatch_active = self._make_color_swatch(
            r1, "COLOR_ACTIVE", "已唱色")
        frame_a.grid(row=0, column=0, sticky="w")
        frame_b, self._swatch_idle = self._make_color_swatch(
            r1, "COLOR_IDLE", "未唱色")
        frame_b.grid(row=0, column=1, sticky="w")
        frame_c, self._swatch_stroke = self._make_color_swatch(
            r1, "COLOR_STROKE", "描边色")
        frame_c.grid(row=0, column=2, sticky="w")

        about_row = tk.Frame(self.ap_container, bg=self.BG)
        about_row.pack(fill="x", pady=(4, 0))

        self._theme_toggle = self._make_toggle_switch(
            about_row, self._toggle_theme, 
            initial=bool(getattr(self.cfg, "DARK_MODE", True)),
            width=40, height=20)
        self._theme_toggle.pack(side="left", padx=(0, 6))
        tk.Label(about_row, text="深色模式", bg=self.BG, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")

        # ★ 播放调整（左右键 seek，秒）
        tk.Label(about_row, text="播放调整", bg=self.BG, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(12, 2))
        self.spn_seek_step = tk.Spinbox(
            about_row, from_=1, to=60, width=3, justify="center",
            bg=self.FIELD, fg=self.FG, relief="flat",
            buttonbackground=self.CARD, insertbackground=self.FG,
            font=("Microsoft YaHei UI", 9),
            command=self._on_seek_step_change)
        self.spn_seek_step.pack(side="left")
        self.spn_seek_step.delete(0, "end")
        self.spn_seek_step.insert(0, str(int(getattr(self.cfg, "SEEK_STEP", 5))))
        self.spn_seek_step.bind("<KeyRelease>", self._on_seek_step_change)
        self.spn_seek_step.bind("<FocusOut>", self._on_seek_step_change)
        self.spn_seek_step.bind("<Return>", self._commit_spinbox)
        self.spn_seek_step.bind("<Escape>", self._commit_spinbox)
        tk.Label(about_row, text="秒", bg=self.BG, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(2, 0))

        # ★ 音量调整（上下键调节，%）
        tk.Label(about_row, text="音量调整", bg=self.BG, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(10, 2))
        self.spn_vol_step = tk.Spinbox(
            about_row, from_=1, to=50, width=3, justify="center",
            bg=self.FIELD, fg=self.FG, relief="flat",
            buttonbackground=self.CARD, insertbackground=self.FG,
            font=("Microsoft YaHei UI", 9),
            command=self._on_vol_step_change)
        self.spn_vol_step.pack(side="left")
        self.spn_vol_step.delete(0, "end")
        self.spn_vol_step.insert(0, str(int(getattr(self.cfg, "VOL_STEP", 5))))
        self.spn_vol_step.bind("<KeyRelease>", self._on_vol_step_change)
        self.spn_vol_step.bind("<FocusOut>", self._on_vol_step_change)
        self.spn_vol_step.bind("<Return>", self._commit_spinbox)
        self.spn_vol_step.bind("<Escape>", self._commit_spinbox)
        tk.Label(about_row, text="%", bg=self.BG, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(2, 0))

        self.btn_about = self._make_round_btn(
            about_row, "ⓘ 关于", self._show_about,
            width=64, height=22, radius=8)
        self.btn_about.pack(side="right")

        # ================= TAB 2：音乐库 =================
        f2 = ttk.Frame(nb, padding=10)
        nb.add(f2, text="歌手")

        top = tk.Frame(f2, bg=self.BG)
        top.pack(fill="x", pady=(0, 6))
        self.ent_search = tk.Entry(top, bg=self.FIELD, fg=self.FG,
                                   insertbackground=self.FG, relief="flat",
                                   highlightthickness=1,
                                   highlightbackground=self.SEL_BG,
                                   highlightcolor=self.ACCENT)
        self.ent_search.pack(side="left", fill="x", expand=True, ipady=4, padx=4)
        self.ent_search.bind("<Return>", lambda _e: self._lib_search())

        # ★ 搜索按钮：与刷新按钮同风格（圆角矩形底 + 文字图标）
        self.btn_search = self._make_round_btn(
            top, "⌕", self._lib_search,
            width=28, height=28, radius=8)
        self.btn_search.pack(side="left", padx=(4, 0))

        # 刷新按钮：圆角矩形
        self.btn_refresh = self._make_round_btn(
            top, "↻", self._lib_refresh,
            width=28, height=28, radius=8)
        self.btn_refresh.pack(side="left", padx=(4, 0))

        cols = tk.Frame(f2, bg=self.BG)
        cols.pack(fill="both", expand=True)
        for i in range(3):
            cols.columnconfigure(i, weight=0)
        cols.rowconfigure(0, weight=1)
        self.cols_frame = cols

        c1 = tk.Frame(cols, bg=self.BG)
        c1.grid(row=0, column=0, sticky="nsew")
        self.col_c1 = c1
        self.lbl_hdr_artists = tk.Label(c1, text="歌手", bg=self.BG, fg=self.DIM,
                                        font=("Microsoft YaHei UI", 9),
                                        anchor="w", padx=4)
        self.lbl_hdr_artists.pack(fill="x")
        self.tree_artists = self._build_tree_column(c1)

        c2 = tk.Frame(cols, bg=self.BG)
        c2.grid(row=0, column=1, sticky="nsew")
        self.col_c2 = c2
        self.lbl_hdr_albums = tk.Label(c2, text="", bg=self.BG, fg=self.DIM,
                                       font=("Microsoft YaHei UI", 9),
                                       anchor="w", padx=4)
        self.lbl_hdr_albums.pack(fill="x")
        self.tree_albums = self._build_tree_column(c2)

        c3 = tk.Frame(cols, bg=self.BG)
        c3.grid(row=0, column=2, sticky="nsew")
        self.col_c3 = c3
        self.lbl_hdr_tracks = tk.Label(c3, text="", bg=self.BG, fg=self.DIM,
                                       font=("Microsoft YaHei UI", 9),
                                       anchor="w", padx=4)
        self.lbl_hdr_tracks.pack(fill="x")

        # 歌曲列：左 Treeview + 右红心 Canvas（红心右对齐）
        list_row = tk.Frame(c3, bg=self.BG)
        list_row.pack(fill="both", expand=True)
        self.tree_tracks_hearts = tk.Canvas(
            list_row, width=18, bg=self.FIELD, highlightthickness=0, bd=0)
        self.tree_tracks_hearts.pack(side="right", fill="y")
        self.tree_tracks = ttk.Treeview(
            list_row, show="tree", selectmode="browse",
            style="Library.Treeview", height=4)
        self.tree_tracks.pack(side="left", fill="both", expand=True)

        c2.grid_remove()
        c3.grid_remove()
        cols.columnconfigure(0, weight=1)

        self.tree_artists.bind("<<TreeviewSelect>>", self._on_artist_selected)
        self.tree_albums.bind("<<TreeviewSelect>>", self._on_album_selected)
        self.tree_tracks.bind("<Double-1>", self._on_track_activate)
        self.tree_tracks.bind(
            "<<TreeviewSelect>>",
            lambda _e: self._redraw_hearts(
                self.tree_tracks, self.tree_tracks_hearts, self._lib_tracks),
            add="+")
        
        # ★ 绑定左右与回车键
        self.tree_artists.bind("<Right>", self._kb_artist_right)
        self.tree_artists.bind("<Return>", self._kb_artist_right)
        self.tree_artists.bind("<Left>", self._kb_artist_left)
        self.tree_artists.bind("<Down>", self._kb_artist_down)
        
        self.tree_albums.bind("<Left>", self._kb_album_left)
        self.tree_albums.bind("<Right>", self._kb_album_right)
        self.tree_albums.bind("<Return>", self._kb_album_right)

        self.tree_tracks.bind("<Left>", self._kb_track_left)
        self.tree_tracks.bind("<Right>", self._kb_track_right)  # 右键不播放
        self.tree_tracks.bind("<Return>", self._kb_track_play)  # 回车播放

        # ★ ESC：收起所有下级列，清空专辑/歌曲高亮，回歌手列
        self.tree_artists.bind("<Escape>", self._kb_escape)
        self.tree_albums.bind("<Escape>", self._kb_escape)
        self.tree_tracks.bind("<Escape>", self._kb_escape)

        self.tree_tracks.bind("<Button-3>", self._on_lib_track_right_click)

        self.tree_tracks.bind(
            "<<TreeviewSelect>>",
            lambda _e: self._redraw_hearts(
                self.tree_tracks, self.tree_tracks_hearts, self._lib_tracks),
            add="+")
        
        bot = tk.Frame(f2, bg=self.BG)
        bot.pack(fill="x", pady=(6, 0))
        self.lbl_lib = tk.Label(bot, text="", bg=self.BG, fg=self.DIM,
                                font=("Microsoft YaHei UI", 9))
        self.lbl_lib.pack(side="left", padx=0)

        # ================= TAB 3：专辑 =================
        f3 = ttk.Frame(nb, padding=10)
        nb.add(f3, text="专辑")
        self.f3 = f3

        ns_top = tk.Frame(f3, bg=self.BG)
        ns_top.pack(fill="x", pady=(0, 6))

        self.lbl_ns_title = tk.Label(ns_top, text="最新专辑", bg=self.BG,
                                     fg=self.DIM,
                                     font=("Microsoft YaHei UI", 9), anchor="w")
        self.lbl_ns_title.pack(side="left", padx=(4, 0))

        self.btn_ns_back = self._make_back_button(ns_top, self._ns_back_to_grid)

        self.btn_ns_refresh = self._mk_btn(ns_top, "↻", self._ns_refresh)
        self.btn_ns_refresh.pack(side="right")

        # --- 网格视图（可滚动，3 列不限行） ---
        self.ns_grid_wrap = tk.Frame(f3, bg=self.BG)
        self.ns_grid_wrap.pack(fill="both", expand=True)

        self.ns_canvas = tk.Canvas(self.ns_grid_wrap, bg=self.BG,
                                   highlightthickness=0, bd=0)
        _ns_vsb = ttk.Scrollbar(self.ns_grid_wrap, orient="vertical",
                                command=self.ns_canvas.yview,
                                style="NewSongs.Vertical.TScrollbar")
        self.ns_canvas.configure(yscrollcommand=_ns_vsb.set)
        _ns_vsb.pack(side="right", fill="y", padx=(2, 0))
        self.ns_canvas.pack(side="left", fill="both", expand=True)

        self.ns_inner = tk.Frame(self.ns_canvas, bg=self.BG)
        self._ns_win = self.ns_canvas.create_window(
            (0, 0), window=self.ns_inner, anchor="nw")

        def _ns_inner_cfg(_e=None):
            try:
                self.ns_canvas.configure(
                    scrollregion=self.ns_canvas.bbox("all"))
            except tk.TclError:
                pass

        def _ns_canvas_cfg(e):
            try:
                self.ns_canvas.itemconfig(self._ns_win, width=e.width)
            except tk.TclError:
                pass

        self.ns_inner.bind("<Configure>", _ns_inner_cfg)
        self.ns_canvas.bind("<Configure>", _ns_canvas_cfg)
        self.ns_canvas.bind("<MouseWheel>", self._ns_on_wheel)
        self.ns_inner.bind("<MouseWheel>", self._ns_on_wheel)

        # --- 专辑歌曲列表视图 ---
        self.ns_list_wrap = tk.Frame(f3, bg=self.BG)

        # 底部信息栏（高度随内容自适应）
        self.ns_bottom_bar = tk.Frame(self.ns_list_wrap, bg=self.CARD)
        self.ns_bottom_bar.pack(side="bottom", fill="x", pady=(8, 0))

        # ① 先打包右侧，锁定其宽度，避免被中间区域挤压
        self.ns_right = tk.Frame(self.ns_bottom_bar, bg=self.CARD)
        self.ns_right.pack(side="right", padx=(12, 12), pady=12, fill="y")
        self.lbl_ns_album = tk.Label(self.ns_right, text="", bg=self.CARD,
                                     fg=self.ACCENT,
                                     font=("Microsoft YaHei UI", 18, "bold"),
                                     anchor="e", justify="right")
        self.lbl_ns_album.pack(fill="x", anchor="e")
        self.lbl_ns_artist = tk.Label(self.ns_right, text="", bg=self.CARD,
                                      fg=self.DIM,
                                      font=("Microsoft YaHei UI", 9),
                                      anchor="e", justify="right", wraplength=170)
        self.lbl_ns_artist.pack(fill="x", anchor="e")

        # ② 再打包左侧封面
        self.ns_album_cover = tk.Canvas(self.ns_bottom_bar, width=72, height=72,
                                        bg=self.CARD, highlightthickness=0, bd=0)
        self.ns_album_cover.pack(side="left", padx=(10, 12), pady=10)

        # ③ 最后打包中间：expand 填满剩余，长歌名只会压缩自身，不再挤右侧
        self.ns_center = tk.Frame(self.ns_bottom_bar, bg=self.CARD)
        self.ns_center.pack(side="left", fill="both", expand=True, pady=12)
        tk.Label(self.ns_center, text="正在播放", bg=self.CARD, fg=self.DIM,
                 font=("Microsoft YaHei UI", 8), anchor="w").pack(
            side="top", fill="x")
        self.lbl_ns_now = tk.Label(self.ns_center, text="—", bg=self.CARD,
                                   fg=self.ACCENT,
                                   font=("Microsoft YaHei UI", 11, "bold"),
                                   anchor="w", justify="left")
        self.lbl_ns_now.pack(side="bottom", fill="x", anchor="w")

        # 列表区（最后 pack 占满剩余空间）
        self.ns_tree_frame = tk.Frame(self.ns_list_wrap, bg=self.BG)
        self.ns_tree_frame.pack(fill="both", expand=True)

        # 滚动条贴最右 → 红心在其内侧 → Treeview 占满剩余
        _ns_tree_vsb = ttk.Scrollbar(self.ns_tree_frame, orient="vertical",
                                     style="NewSongs.Vertical.TScrollbar")
        _ns_tree_vsb.pack(side="right", fill="y", padx=(2, 0))

        self.ns_tree_hearts = tk.Canvas(
            self.ns_tree_frame, width=18, bg=self.FIELD,
            highlightthickness=0, bd=0)
        self.ns_tree_hearts.pack(side="right", fill="y")

        self.ns_tree = ttk.Treeview(self.ns_tree_frame, show="tree",
                                    selectmode="browse",
                                    style="Library.Treeview", height=4)
        self.ns_tree.pack(side="left", fill="both", expand=True)

        def _on_ns_yscroll(first, last):
            _ns_tree_vsb.set(first, last)
            try:
                self.panel.after_idle(
                    lambda: self._redraw_hearts(
                        self.ns_tree, self.ns_tree_hearts, self._ns_tracks))
            except tk.TclError:
                pass
        self.ns_tree.configure(yscrollcommand=_on_ns_yscroll)

        self.ns_tree.bind("<Double-1>", self._on_ns_track_activate)
        self.ns_tree.bind("<Return>", self._on_ns_track_activate)
        self.ns_tree.bind("<Escape>", self._ns_on_escape)
        self.ns_tree.bind("<Button-3>", self._on_ns_track_right_click)
        self.ns_tree.bind(
            "<<TreeviewSelect>>",
            lambda _e: self._redraw_hearts(
                self.ns_tree, self.ns_tree_hearts, self._ns_tracks),
            add="+")
                
        # 网格封面下载线程池
        for _ in range(4):
            threading.Thread(target=self._ns_cover_worker,
                             daemon=True).start()     
        # ================= TAB 4：收藏 =================
        f4 = ttk.Frame(nb, padding=10)
        nb.add(f4, text="收藏")
        self.f4 = f4

        fav_top = tk.Frame(f4, bg=self.BG)
        fav_top.pack(fill="x", pady=(0, 6))
        self.lbl_fav_title = tk.Label(fav_top, text="收藏的歌曲", bg=self.BG,
                                      fg=self.DIM,
                                      font=("Microsoft YaHei UI", 9), anchor="w")
        self.lbl_fav_title.pack(side="left", padx=(4, 0))
        self.btn_fav_refresh = self._make_round_btn(
            fav_top, "↻", self._fav_refresh,
            width=28, height=28, radius=8)
        self.btn_fav_refresh.pack(side="right")

        fav_wrap = tk.Frame(f4, bg=self.BG)
        fav_wrap.pack(fill="both", expand=True)
        self.fav_tree = ttk.Treeview(fav_wrap, show="tree", selectmode="browse",
                                     style="Library.Treeview", height=10)
        fav_vsb = ttk.Scrollbar(fav_wrap, orient="vertical",
                                command=self.fav_tree.yview,
                                style="NewSongs.Vertical.TScrollbar")
        self.fav_tree.configure(yscrollcommand=fav_vsb.set)
        fav_vsb.pack(side="right", fill="y", padx=(2, 0))
        self.fav_tree.pack(side="left", fill="both", expand=True)

        self.fav_tree.bind("<Double-1>", self._on_fav_activate)
        self.fav_tree.bind("<Return>", self._on_fav_activate)
        self.fav_tree.bind("<Button-3>", self._on_fav_right_click)

        # ================= TAB 5：歌单 =================
        f5 = ttk.Frame(nb, padding=10)
        nb.add(f5, text="歌单")
        self.f5 = f5

        pl_top = tk.Frame(f5, bg=self.BG)
        pl_top.pack(fill="x", pady=(0, 6))
        self.lbl_pl_title = tk.Label(pl_top, text="歌单", bg=self.BG,
                                     fg=self.DIM,
                                     font=("Microsoft YaHei UI", 9), anchor="w")
        self.lbl_pl_title.pack(side="left", padx=(4, 0))
        self.btn_pl_refresh = self._make_round_btn(
            pl_top, "↻", self._pl_refresh,
            width=28, height=28, radius=8)
        self.btn_pl_refresh.pack(side="right")

        pl_cols = tk.Frame(f5, bg=self.BG)
        pl_cols.pack(fill="both", expand=True)

        # 左：歌单名列表
        pl_left = tk.Frame(pl_cols, bg=self.BG, width=170)
        pl_left.pack(side="left", fill="y", padx=0)
        pl_left.pack_propagate(False)
        self.pl_tree = ttk.Treeview(pl_left, show="tree", selectmode="browse",
                                    style="Library.Treeview", height=10)
        self.pl_tree.pack(fill="both", expand=True)

        # 右：选中歌单的歌曲列表
        pl_right = tk.Frame(pl_cols, bg=self.BG)
        pl_right.pack(side="left", fill="both", expand=True)
        self.pl_tracks_tree = ttk.Treeview(pl_right, show="tree",
                                           selectmode="browse",
                                           style="Library.Treeview", height=10)
        self.pl_tracks_tree.pack(fill="both", expand=True)

        self.pl_tree.bind("<<TreeviewSelect>>", self._on_pl_selected)
        self.pl_tree.bind("<Double-1>", self._on_pl_double)
        self.pl_tree.bind("<Return>", self._on_pl_enter)
        self.pl_tree.bind("<Button-3>", self._on_pl_right_click)
        self.pl_tracks_tree.bind("<Double-1>", self._on_pl_track_double)
        self.pl_tracks_tree.bind("<Return>", self._on_pl_track_double)
        self.pl_tracks_tree.bind("<Button-3>", self._on_pl_track_right_click)
                        
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _build_tree_column(self, parent):
        tv = ttk.Treeview(parent, show="tree", selectmode="browse",
                          style="Library.Treeview", height=4)
        tv.pack(fill="both", expand=True)
        return tv

    def _mk_btn(self, parent, text, cmd, width=None):
        kw = dict(text=text, command=cmd, bg=self.CARD, fg=self.FG,
                  activebackground=self.SEL_BG, activeforeground=self.FG,
                  relief="flat", font=("Microsoft YaHei UI", 10),
                  padx=8, pady=3)
        if width:
            kw["width"] = width
        return tk.Button(parent, **kw)

    # ================= 主题切换逻辑 =================
    def _toggle_theme(self):
        is_dark = bool(getattr(self.cfg, "DARK_MODE", True))
        self.cfg.DARK_MODE = not is_dark
        try:
            self.cfg.save()
        except Exception:
            pass

        self._theme_toggle.set_value(self.cfg.DARK_MODE)
        self._apply_theme()

    def _apply_titlebar_theme(self):
        """把主面板标题栏同步为当前主题。"""
        if self._compact_mode:
            return
        try:
            if self.cfg.DARK_MODE:
                apply_dark_titlebar(self.panel, dark=True, bg_color=self.BG)
            else:
                apply_dark_titlebar(self.panel, dark=False)
        except Exception:
            pass
    
    def _sel_highlight_color(self):
        """与 _build_style 里 Library.Treeview 的 selected 背景色保持一致。"""
        return "#5A5AE0" if self.cfg.DARK_MODE else "#4A4AE0"
    
    def _apply_theme(self):
        if self.cfg.DARK_MODE:
            old_colors = {
                "#F0F2F5": "#15151E", "#FFFFFF": "#20202E",
                "#1A1A24": "#E8E8F0", "#5A5A6A": "#9A9AB0",
                "#E0E0E8": "#3D3D5C", "#4A4AE0": "#5A5AE0"
            }
            self.BG = "#15151E"; self.CARD = "#20202E"; self.FIELD = "#1A1A24"
            self.FG = "#E8E8F0"; self.DIM = "#9A9AB0"; self.SEL_BG = "#3D3D5C"
            self.ACCENT = "#5A5AE0"
            self.HOVER_BG = "#3D3D5C"
            self.HOVER_ACCENT = "#7A7AFF"
        else:
            old_colors = {
                "#15151E": "#F0F2F5", "#20202E": "#FFFFFF",
                "#E8E8F0": "#1A1A24", "#9A9AB0": "#5A5A6A",
                "#3D3D5C": "#E0E0E8", "#5A5AE0": "#4A4AE0"
            }
            self.BG = "#F0F2F5"; self.CARD = "#FFFFFF"; self.FIELD = "#FFFFFF"
            self.FG = "#1A1A24"; self.DIM = "#5A5A6A"; self.SEL_BG = "#E0E0E8"
            self.ACCENT = "#4A4AE0"
            self.HOVER_BG = "#E0E0E8"
            self.HOVER_ACCENT = "#7A7AFF"

        self.panel.configure(bg=self.BG)
        self._recolor_widgets(self.panel, old_colors)
        self._build_style()

        if hasattr(self, "_toggle_cache"): self._toggle_cache.clear()
        if hasattr(self, "_circle_cache"): self._circle_cache.clear()
        self._icon_cache.clear()
        if hasattr(self, "_heart_cache"): self._heart_cache.clear()        
        if hasattr(self, "_back_icon_cache"): self._back_icon_cache.clear()
        self._ns_grid_panel_h = None
        if hasattr(self, "_pin_icon_cache"): self._pin_icon_cache.clear()
        if hasattr(self, "_server_icon_cache"): self._server_icon_cache.clear()
        if hasattr(self, "_speaker_photo_cache"): self._speaker_photo_cache.clear()

        self._update_custom_widgets(self.panel)
        self._lib_refresh()
        
        try:
            self.spn_size.config(
                bg=self.FIELD, fg=self.FG, 
                buttonbackground=self.CARD, 
                insertbackground=self.FG
            )
            try:
                self.spn_seek_step.config(
                    bg=self.FIELD, fg=self.FG,
                    buttonbackground=self.CARD,
                    insertbackground=self.FG)
                self.spn_vol_step.config(
                    bg=self.FIELD, fg=self.FG,
                    buttonbackground=self.CARD,
                    insertbackground=self.FG)
            except (AttributeError, tk.TclError):
                pass            
            self.ent_search.config(
                bg=self.FIELD, fg=self.FG, 
                insertbackground=self.FG,
                highlightbackground=self.SEL_BG, 
                highlightcolor=self.ACCENT
            )
            
            self.lbl_title.config(bg=self.CARD, fg=self.FG)
            self.lbl_artist.config(bg=self.CARD, fg=self.DIM)
            self.lbl_time.config(bg=self.BG, fg=self.DIM)
            self.lbl_server.config(bg=self.BG, fg=self.ACCENT)
            self.lbl_player.config(bg=self.BG, fg=self.ACCENT)
            
            self.lbl_hdr_artists.config(bg=self.BG, fg=self.DIM)
            self.lbl_hdr_albums.config(bg=self.BG, fg=self.DIM)
            self.lbl_hdr_tracks.config(bg=self.BG, fg=self.DIM)
            self.lbl_lib.config(bg=self.BG, fg=self.DIM)
        except Exception:
            pass
        # ★ 修复爱心 Canvas 背景色：
        #   _recolor_widgets 会把 FIELD(#1A1A24) 误识别为 FG 而互换色值，
        #   这里显式重设为当前主题的 FIELD 色，覆盖错误映射。
        try:
            self.tree_tracks_hearts.config(bg=self.FIELD)
        except Exception:
            pass
        try:
            self.ns_tree_hearts.config(bg=self.FIELD)
        except Exception:
            pass

        # 背景色变了，重绘红心（顺带用新主题的高亮色重绘选中行底色）
        try:
            self._redraw_hearts(
                self.tree_tracks, self.tree_tracks_hearts, self._lib_tracks)
        except Exception:
            pass
        try:
            self._redraw_hearts(
                self.ns_tree, self.ns_tree_hearts, self._ns_tracks)
        except Exception:
            pass        
        try:
            self._apply_titlebar_theme()
        except Exception:
            pass
        try:
            new_photo = self._get_speaker_photo(14)
            if new_photo is not None:
                self.lbl_player.config(image=new_photo)
                self.lbl_player.image = new_photo
        except Exception:
            pass        
        
    def _recolor_widgets(self, widget, mapping):
        try:
            for prop in ("bg", "fg", "activebackground", "activeforeground", 
                         "highlightbackground", "highlightcolor", "insertbackground", 
                         "buttonbackground", "troughcolor"):
                if prop in widget.keys():
                    val = widget.cget(prop)
                    if val in mapping:
                        widget.configure(**{prop: mapping[val]})
        except Exception:
            pass
        
        for child in widget.winfo_children():
            self._recolor_widgets(child, mapping)

    def _update_custom_widgets(self, widget):
        if hasattr(widget, "_update_theme"):
            try:
                widget._update_theme(self)
            except Exception:
                pass
        for child in widget.winfo_children():
            self._update_custom_widgets(child)

    # ================= 播放控制 =================
    def _cmd(self, what):
        try:
            if what == "prev":
                if self._play_mode == "queue":
                    self.lms.prev_track_queue()
                else:
                    self.lms.prev_track()
            elif what == "next":
                if self._play_mode == "queue":
                    self.lms.next_track_queue()
                else:
                    self.lms.next_track()
        except Exception:
            traceback.print_exc()

    def _toggle_play(self):
        now = time.time()
        if now - self._last_play_press < 0.5:
            return
        self._last_play_press = now
        try:
            self.lms.command(["pause"])
        except Exception:
            traceback.print_exc()

    def _place_panel_initial(self, height):
        """首次启动：显示在显示器正中心；否则恢复上次记忆的位置。"""
        w = 500
        try:
            sw = self.panel.winfo_screenwidth()
            sh = self.panel.winfo_screenheight()
        except tk.TclError:
            sw, sh = 1920, 1080

        try:
            px = int(getattr(self.cfg, "PANEL_X", -1))
            py = int(getattr(self.cfg, "PANEL_Y", -1))
        except (TypeError, ValueError):
            px, py = -1, -1

        # 未设置 → 居中
        if px < 0 or py < 0:
            px = max(0, (sw - w) // 2)
            py = max(0, (sh - height) // 2)
            try:
                self.cfg.PANEL_X = px
                self.cfg.PANEL_Y = py
            except Exception:
                pass

        try:
            self.panel.geometry(f"{w}x{height}+{px}+{py}")
        except tk.TclError:
            pass

    def save_position(self):
        """保存主面板当前位置（简洁模式下不保存，避免记忆浮窗坐标）。"""
        if self._compact_mode:
            return
        try:
            self.cfg.PANEL_X = self.panel.winfo_x()
            self.cfg.PANEL_Y = self.panel.winfo_y()
        except Exception:
            pass
        
    def _calc_panel_height(self):
        try:
            cur = self.nb.index(self.nb.select())
        except Exception:
            cur = 0
        if cur == 2:
            return 480 if self._ns_view == "list" else 720
        if cur in (3, 4):
            return 480
        return 490 if self._lyrics_appearance_visible else 365

    def _on_tab_changed(self, _e=None):
        if self._compact_mode:
            return
        try:
            cur = self.nb.index(self.nb.select())
        except Exception:
            return

        if cur == 0:
            # 播放中：显示「⚙」按钮
            self.btn_settings.pack_forget()
            self.btn_settings.pack(side="left", padx=(0, 6),
                                   before=self.btn_lyrics)
            try:
                self.panel.focus_set()
            except tk.TclError:
                pass

        elif cur == 1:
            # 音乐库：隐藏「⚙」
            self.btn_settings.pack_forget()
            if not self.tree_artists.get_children():
                self._lib_show_artists()

        elif cur == 2:
            # 新歌曲：隐藏「⚙」，高度走探针法，直接返回
            self.btn_settings.pack_forget()
            self._ns_ensure_loaded()
            if self._ns_view == "list":
                self._set_panel_height(480)
            else:
                self.panel.after(20, self._ns_fit_grid_height)
            return
        elif cur == 3:
            self.btn_settings.pack_forget()
            self._fav_ensure_loaded()
            self._set_panel_height(480)
            return

        elif cur == 4:
            self.btn_settings.pack_forget()
            self._pl_ensure_loaded()
            self._set_panel_height(480)
            return
        
        # 播放中 / 音乐库：按各自模式恢复面板高度
        h = self._calc_panel_height()
        self.panel.update_idletasks()
        try:
            self.panel.geometry(f"500x{h}")
        except tk.TclError:
            pass

    def _toggle_lyrics_panel(self):
        if self._lyrics_appearance_visible:
            self.ap_container.pack_forget()
            self._lyrics_appearance_visible = False
        else:
            self.ap_container.pack(fill="x")
            self._lyrics_appearance_visible = True

        h = self._calc_panel_height()
        self.panel.update_idletasks()
        try:
            self.panel.geometry(f"500x{h}")
        except tk.TclError:
            pass

    def _set_vol_pct(self, v):
        """更新音量百分比标签（容错：标签可能尚未构建或被销毁）"""
        try:
            pct = max(0, min(100, int(round(float(v)))))
            self.lbl_vol_pct.config(text=f"{pct}%")
        except (tk.TclError, ValueError, TypeError, AttributeError):
            pass

    def _on_volume(self, val):
        try:
            v = int(float(val))
            self.lms.command(["mixer", "volume", str(v)])
        except Exception:
            v = None
        if v is not None:
            self._set_vol_pct(v)

    def _on_vol_drag_start(self):
        self._vol_dragging = True

    def _on_vol_drag_end(self):
        self.panel.after(300, self._end_vol_drag)

    def _end_vol_drag(self):
        self._vol_dragging = False

    def _seek_start(self):
        self._seeking = True

    def _seek_end(self, _e=None):
        if not self._seeking:
            return
        self._seeking = False
        try:
            pos = float(self.progress.get())
            if self._duration:
                self.song_pos = pos
                self._last_seek_ts = time.time()

                def work(p=pos):
                    try:
                        self.lms.seek(p)
                    except Exception:
                        traceback.print_exc()
                threading.Thread(target=work, daemon=True).start()
        except Exception:
            traceback.print_exc()

    # ================= 外观设置 =================
    def _font_families(self):
        fams = sorted({f for f in tkfont.families()
                       if f and not f.startswith(("@", "＠"))})
        prefer = ["Microsoft YaHei UI", "微软雅黑", "Microsoft YaHei",
                  "SimHei", "Segoe UI"]
        head = [f for f in prefer if f in fams]
        return head + [f for f in fams if f not in head]

    def _sync_font_controls(self):
        self.cmb_font.set(self.cfg.FONT_FAMILY)
        self.spn_size.delete(0, "end")
        self.spn_size.insert(0, str(self.cfg.FONT_SIZE))
        self.cmb_font.bind("<<ComboboxSelected>>", self._on_font_change)
        self.spn_size.bind("<KeyRelease>", self._on_font_change)
        self.spn_size.config(command=self._on_font_change)

    def _on_font_change(self, _e=None):
        try:
            size = int(float(self.spn_size.get()))
        except ValueError:
            return
        fam = self.cmb_font.get()
        if fam.startswith(("@", "＠")) or not fam:
            return
        self.cfg.FONT_FAMILY = fam
        self.cfg.FONT_SIZE = max(12, min(72, size))
        self.cfg.save()
        
        main, trans, next_t, _ = self._get_current_texts()
        self.overlay.set_line(main, next_t, self.track_info.get("title", ""), trans)

    def _on_seek_step_change(self, _e=None):
        """读取进度步进 Spinbox 并持久化；非法值在失焦时自动恢复"""
        txt = self.spn_seek_step.get().strip()
        try:
            val = int(float(txt))
            if 1 <= val <= 60:
                self.cfg.SEEK_STEP = val
                try:
                    self.cfg.save()
                except Exception:
                    pass
                return
        except (ValueError, tk.TclError):
            pass
        # 非法值：失焦时自动恢复为当前配置
        try:
            if self.panel.focus_get() != self.spn_seek_step:
                self.spn_seek_step.delete(0, "end")
                self.spn_seek_step.insert(
                    0, str(int(getattr(self.cfg, "SEEK_STEP", 5))))
        except tk.TclError:
            pass

    def _on_vol_step_change(self, _e=None):
        """读取音量步进 Spinbox 并持久化；非法值在失焦时自动恢复"""
        txt = self.spn_vol_step.get().strip()
        try:
            val = int(float(txt))
            if 1 <= val <= 50:
                self.cfg.VOL_STEP = val
                try:
                    self.cfg.save()
                except Exception:
                    pass
                return
        except (ValueError, tk.TclError):
            pass
        try:
            if self.panel.focus_get() != self.spn_vol_step:
                self.spn_vol_step.delete(0, "end")
                self.spn_vol_step.insert(
                    0, str(int(getattr(self.cfg, "VOL_STEP", 5))))
        except tk.TclError:
            pass

    def _pick_color(self, key):
        current = getattr(self.cfg, key)
        _rgb, hexcolor = colorchooser.askcolor(color=current, parent=self.panel,
                                               title="选择歌词颜色")
        if hexcolor:
            setattr(self.cfg, key, hexcolor)
            self.cfg.save()
            swatch_map = {
                "COLOR_ACTIVE": self._swatch_active,
                "COLOR_IDLE":   self._swatch_idle,
                "COLOR_STROKE": self._swatch_stroke,
            }
            sw = swatch_map.get(key)
            if sw:
                sw()
            
            main, trans, next_t, _ = self._get_current_texts()
            self.overlay.set_line(main, next_t, self.track_info.get("title", ""), trans)

    # ================= 关于 / 更新 =================
    def _open_url(self, url):
        try:
            webbrowser.open(url)
        except Exception:
            traceback.print_exc()

    # ================= 播放器切换与监控 =================
    def _update_players(self, players):
        self._available_players = players

        current_mac = self.lms.player_mac()
        current_name = ""
        is_online = False
        new_added = False

        for mac, name in players:
            if mac == current_mac:
                current_name = name
                is_online = True
            if mac not in self._known_player_macs:
                self._known_player_macs.add(mac)
                new_added = True

        arrow = " ▼" if len(players) >= 2 else ""

        if not players:
            self.lbl_player.config(text=" (无可用)", fg=self.DIM)
        elif not is_online:
            self.lbl_player.config(text=f" (离线){arrow}", fg="#FF8080")
        else:
            if self.player_name != current_name:
                self.player_name = current_name
            if new_added:
                self.lbl_player.config(
                    text=f" {current_name} (新){arrow}", fg=self.ACCENT)
            else:
                # ★ 播放器名称用高亮色，提示可点击切换
                self.lbl_player.config(
                    text=f" {current_name}{arrow}", fg=self.ACCENT)

    def _show_players_menu(self, event):
        menu = tk.Menu(self.panel, tearoff=0,
                       bg=self.CARD, fg=self.FG,
                       activebackground=self.ACCENT,
                       activeforeground="#FFFFFF",
                       font=("Microsoft YaHei UI", 9))

        if not self._available_players:
            menu.add_command(label="暂无可用播放器", state="disabled")
        else:
            current_mac = self.lms.player_mac()
            for mac, name in self._available_players:
                prefix = "● " if mac == current_mac else "   "
                menu.add_command(label=f"{prefix}{name}",
                                 command=lambda m=mac, n=name: self._on_player_changed(m, n))

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_player_changed(self, mac, name):
        if mac == self.lms.player_mac():
            return

        print(f"[面板] 手动切换播放器: {name} ({mac})")
        self.lms.set_player_mac(mac)
        self.cfg.PLAYER_MAC = mac
        try:
            self.cfg.save()
        except Exception:
            pass

        self.player_name = name
        self.lbl_player.config(text=f" {name} ▼", fg=self.ACCENT)

        self._current_cover_id = ""
        self._cover_cache.clear()
        self._cover_photo = None
        self.lines = []
        self.cur_line_idx = -1
        self._last_idx = -1
        self._lyrics_source = ""
        self.track_info = {}
        self._vol_set = False

    def _open_lms_web(self, _e=None):
        if getattr(self, "lms", None) and getattr(self.lms, "host", ""):
            self._open_url(f"http://{self.lms.host}:{self.lms.port}")

    def _open_daphile_settings(self):
        if getattr(self, "lms", None) and getattr(self.lms, "host", ""):
            self._open_url(f"http://{self.lms.host}/cgi-bin/Settings")

    def _show_about(self):
        win = tk.Toplevel(self.panel)
        win.title("关于")
        win.geometry("540x320")
        win.configure(bg=self.BG)
        win.resizable(False, False)
        win.transient(self.panel)
        win.grab_set()

        try:
            win.iconbitmap(_resource_path("icon.ico"))
        except Exception:
            pass

        win.update_idletasks()
        try:
            if self.cfg.DARK_MODE:
                apply_dark_titlebar(win, dark=True, bg_color=self.BG)
            else:
                apply_dark_titlebar(win, dark=False)
        except Exception:
            pass        
        px = self.panel.winfo_rootx() + (self.panel.winfo_width() - 540) // 2
        py = self.panel.winfo_rooty() + (self.panel.winfo_height() - 320) // 2
        win.geometry(f"+{px}+{py}")

        tk.Label(win, text="达菲播放器", bg=self.BG, fg=self.FG,
                 font=("Microsoft YaHei UI", 15, "bold")).pack(pady=(16, 4))

        tk.Label(win, text=f"版本 {APP_VERSION}", bg=self.BG, fg=self.DIM,
                 font=("Microsoft YaHei UI", 10)).pack()

        tk.Frame(win, bg=self.SEL_BG, height=1).pack(fill="x", padx=24, pady=10)

        row = tk.Frame(win, bg=self.BG)
        row.pack(fill="x", padx=24, pady=2)
        tk.Label(row, text="GitHub:", bg=self.BG, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        link = tk.Label(row, text=GITHUB_URL, bg=self.BG, fg=self.ACCENT,
                        font=("Microsoft YaHei UI", 8, "underline"),
                        cursor="hand2", wraplength=430, justify="left")
        link.pack(side="left", padx=(4, 0), fill="x", expand=True)
        link.bind("<Button-1>", lambda _e: self._open_url(GITHUB_URL))

        intro_text = (
            "本软件在Github免费开源，旨在为广大音乐发烧友提供一款简洁的"
            "达菲桌面播放器。为更好运行本软件，你需要提前在达菲系统安装"
            "Material Skin和Music and Artist Information插件。"
        )
        intro = tk.Label(win, text=intro_text, bg=self.BG, fg=self.DIM,
                         font=("Microsoft YaHei UI", 9), wraplength=490, justify="left")
        intro.pack(fill="x", padx=24, pady=(10, 4))

        btns = tk.Frame(win, bg=self.BG)
        btns.pack(side="bottom", fill="x", padx=24, pady=(8, 12))

        self._about_check_btn = self._make_round_btn(
            btns, "检查更新", lambda: self._check_update(auto=False),
            width=96, height=28, radius=10)
        self._about_check_btn.pack(side="left")

        tk.Label(btns, text="自动检查更新", bg=self.BG, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(16, 6))

        self._about_auto_toggle = self._make_toggle_switch(
            btns, self._toggle_auto_check,
            initial=bool(getattr(self.cfg, "AUTO_CHECK_UPDATE", True)))
        self._about_auto_toggle.pack(side="left")

        self._about_status_lbl = tk.Label(win, text="", bg=self.BG, fg=self.DIM,
                                          font=("Microsoft YaHei UI", 9))
        self._about_status_lbl.pack(side="bottom", pady=(0, 4))

    def _toggle_auto_check(self):
        cur = bool(getattr(self.cfg, "AUTO_CHECK_UPDATE", True))
        self.cfg.AUTO_CHECK_UPDATE = not cur
        try:
            self.cfg.save()
        except Exception:
            pass
        try:
            if self._about_auto_toggle is not None:
                self._about_auto_toggle.set_value(self.cfg.AUTO_CHECK_UPDATE)
        except tk.TclError:
            pass

        if self.cfg.AUTO_CHECK_UPDATE:
            self._check_update(auto=False)

    def _check_update(self, auto=False):
        if self._update_checking:
            return
        self._update_checking = True

        if not auto:
            try:
                if self._about_status_lbl is not None:
                    self._about_status_lbl.config(text="正在检查更新…", fg=self.DIM)
            except tk.TclError:
                pass

        def work():
            latest = ""
            err = ""
            try:
                req = urllib.request.Request(
                    RELEASES_API, headers={"User-Agent": "lms-karaoke", "Accept": "application/vnd.github+json"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read().decode("utf-8"))
                    latest = (data.get("tag_name") or "").strip()
            except Exception as e:
                err = str(e)

            def apply():
                self._update_checking = False
                if err or not latest:
                    if not auto:
                        try:
                            if self._about_status_lbl is not None:
                                self._about_status_lbl.config(text=f"检查失败: {err[:40]}", fg="#FF8080")
                        except tk.TclError:
                            pass
                    return

                has_new = self._version_greater(latest, APP_VERSION)
                if has_new:
                    try:
                        if self._ask_yesno("发现新版本",
                                           f"当前版本 {APP_VERSION}\n发现新版 {latest}\n\n是否前往下载？"):
                            self._open_url(DOWNLOAD_URL)
                    except tk.TclError:
                        pass
                    if not auto:
                        try:
                            if self._about_status_lbl is not None:
                                self._about_status_lbl.config(text=f"最新版本: {latest}", fg=self.ACCENT)
                        except tk.TclError:
                            pass
                else:
                    if not auto:
                        try:
                            if self._about_status_lbl is not None:
                                self._about_status_lbl.config(text=f"已是最新版本 ({APP_VERSION})", fg=self.ACCENT)
                        except tk.TclError:
                            pass

            try:
                self.panel.after(0, apply)
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _version_greater(a, b):
        def parse(s):
            s = (s or "").strip().lstrip("vV")
            nums = []
            for part in s.split("."):
                m = ""
                for ch in part:
                    if ch.isdigit(): m += ch
                    else: break
                nums.append(int(m) if m else 0)
            return nums
        na, nb = parse(a), parse(b)
        while len(na) < len(nb): na.append(0)
        while len(nb) < len(na): nb.append(0)
        return na > nb

    # ================= 状态轮询 =================
    def _poll_loop(self):
        last_track = None
        while not self._stop:
            try:
                st = self.lms.get_status()
                in_grace = (time.time() - self._last_seek_ts) <= 1.5

                # seek 保护期：不用 LMS 旧值覆写 song_pos，避免进度条弹回
                if not in_grace:
                    self.song_pos = st.get("time", 0.0)
                self._duration = st.get("duration", 0.0)

                # seek 保护期：如果本地是暂停态，忽略 LMS 瞬时的 "play" 通知，
                # 避免播放/暂停按钮闪烁、避免误判为已播放
                new_playing = st.get("mode") == "play"
                if in_grace and (not self._playing) and new_playing:
                    pass  # 保持暂停显示
                else:
                    self._playing = new_playing
                if "volume" in st and not self._vol_dragging:
                    try:
                        v = float(st["volume"])
                        if abs(v - float(self.scl_vol.get())) > 0.5:
                            self.scl_vol.set(v)
                        # ★ 后台线程更新 UI 走 after
                        self.panel.after(0, lambda vv=v: self._set_vol_pct(vv))
                    except (TypeError, ValueError):
                        pass
                tid = st.get("track_id")
                if tid != last_track:
                    last_track = tid
                    self.track_info = st
                    self._start_fetch(st)
                self._update_cover(st)

                now = time.time()
                if now - self._last_players_check > 5.0:
                    self._last_players_check = now
                    try:
                        players = self.lms.list_players()
                        self.panel.after(0, lambda p=players: self._update_players(p))
                    except Exception:
                        pass
            except Exception:
                time.sleep(1.0)
                continue
            time.sleep(self.cfg.POLL_INTERVAL)

    def _start_fetch(self, st):
        self.lines = []
        self.cur_line_idx = -1
        self._last_idx = -1
        self._lyrics_source = ""
        token = self._fetch_token = self._fetch_token + 1
        track_id = st.get("track_id")
        if not track_id:
            self._lyrics_source = "未找到"
            return

        def work():
            try:
                src, lrc = self.fetcher.fetch(track_id, st.get("artist", ""), st.get("title", ""), st.get("album", ""), st.get("duration", 0))
            except Exception:
                traceback.print_exc()
                src, lrc = "未找到", ""

            def apply():
                if token != self._fetch_token:
                    return
                if lrc:
                    self.lines = LrcParser.parse(lrc)
                    self._lyrics_source = src
                else:
                    self.lines = []
                    self._lyrics_source = "未找到"
                self.cur_line_idx = -1
                self._last_idx = -1
            self.panel.after(0, apply)

        threading.Thread(target=work, daemon=True).start()

    # ================= 专辑封面 =================
    def _draw_default_cover(self):
        c = self.cover_canvas
        c.delete("all")
        cx = cy = 36
        c.create_oval(cx - 32, cy - 32, cx + 32, cy + 32,
                      fill="#2A2A3A" if self.cfg.DARK_MODE else "#D0D0D8", 
                      outline="#3D3D5C", width=2)
        c.create_oval(cx - 14, cy - 14, cx + 14, cy + 14,
                      fill=self.ACCENT, outline="")
        c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3,
                      fill=self.CARD, outline="")
        c.create_arc(cx - 26, cy - 26, cx + 26, cy + 26, start=30, extent=60,
                     style="arc", outline="#4A4A6A", width=2)
        self._cover_photo = None
        if self._compact_mode:
            self._copy_cover_to_compact()

    def _set_cover_image(self, photo):
        c = self.cover_canvas
        c.delete("all")
        self._cover_photo = photo
        c.create_image(36, 36, image=photo)
        if self._compact_mode:
            self._copy_cover_to_compact()

    def _update_cover(self, st):
        cover_id = str(st.get("coverid", "") or "").strip()
        track_id = str(st.get("track_id", "") or "").strip()

        if cover_id and cover_id not in ("0", "-"):
            key = "c:" + cover_id
        elif track_id:
            key = "t:" + track_id
        else:
            key = ""

        if not key:
            if self._current_cover_id:
                self._current_cover_id = ""
                self.panel.after(0, self._draw_default_cover)
            return

        if key == self._current_cover_id:
            return
        self._current_cover_id = key

        if key in self._cover_cache:
            photo = self._cover_cache[key]
            self.panel.after(0, lambda p=photo: self._set_cover_image(p))
            return

        self.panel.after(0, self._draw_default_cover)
        if HAS_PIL:
            self._fetch_cover(key, cover_id, track_id)

    def _fetch_cover(self, key, cover_id, track_id):
        host, port = self.lms.host, self.lms.port
        mac = ""
        try:
            mac = self.lms.player_mac() or ""
        except Exception:
            pass

        urls = []
        if cover_id and cover_id not in ("0", "-"):
            urls += [f"http://{host}:{port}/music/{cover_id}/cover.jpg", f"http://{host}:{port}/music/{cover_id}/cover_100x100.jpg", f"http://{host}:{port}/music/{cover_id}/cover_72x72.jpg"]
        if track_id:
            urls += [f"http://{host}:{port}/music/{track_id}/cover.jpg", f"http://{host}:{port}/music/{track_id}/cover_100x100.jpg", f"http://{host}:{port}/music/{track_id}/cover_72x72.jpg"]
        if mac:
            urls.append(f"http://{host}:{port}/music/current/cover.jpg?player={mac}")

        def is_image(data):
            if not data or len(data) < 8: return False
            return (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[:6] in (b"GIF87a", b"GIF89a") or data[:2] == b"BM")

        def work():
            for url in urls:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "lms-karaoke/1.0"})
                    with urllib.request.urlopen(req, timeout=6) as r:
                        data = r.read()
                    if is_image(data):
                        print(f"[封面] 命中: {url} ({len(data)} bytes)")
                        self.panel.after(0, lambda d=data, k=key: self._on_cover_downloaded(k, d))
                        return
                except Exception:
                    pass
            print(f"[封面] 全部 URL 失败 key={key}")

        threading.Thread(target=work, daemon=True).start()

    def _on_cover_downloaded(self, key, data):
        if key != self._current_cover_id:
            return
        try:
            img = Image.open(BytesIO(data)).convert("RGB")
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:
                resample = Image.LANCZOS
            img = img.resize((72, 72), resample)
            photo = ImageTk.PhotoImage(img)
            self._cover_cache[key] = photo
            self._set_cover_image(photo)
        except Exception as e:
            print(f"[封面] 解析失败: {e}")

    # ================= UI 心跳 =================
    def _ui_tick_loop(self):
        if self._stop:
            return
        try:
            self._ui_tick()
        except Exception:
            traceback.print_exc()
        self.panel.after(100, self._ui_tick_loop)

    def _get_current_texts(self):
        """提取当前的主行、翻译行、下一句以及该句的结束时间"""
        if not self.lines or self.cur_line_idx < 0 or self.cur_line_idx >= len(self.lines):
            return "", "", "", 0.0
        
        idx = self.cur_line_idx
        t0 = self.lines[idx][0]
        
        current_lines = [self.lines[idx][1]]
        
        # ★ 向下查找：严格容差（< 0.6 秒）内才判定为翻译行
        j = idx + 1
        while j < len(self.lines):
            t_next = self.lines[j][0]
            if t_next - t0 < 0.6 and self.lines[j][1] not in current_lines:
                current_lines.append(self.lines[j][1])
                j += 1
            else:
                break
                
        main_text = current_lines[0]
        trans_text = current_lines[1] if len(current_lines) > 1 else ""
        next_text = self.lines[j][1] if j < len(self.lines) else ""
        
        # 计算结束时间
        t1 = self.lines[j][0] if j < len(self.lines) else t0 + max(3.0, len(main_text) * 0.28)
        
        return main_text, trans_text, next_text, t1

    def _ui_tick(self):
        info = self.track_info
        title = info.get("title", "") or "—"
        artist = " / ".join(x for x in (info.get("artist", ""), info.get("album", "")) if x)
        self.lbl_title.config(text=title)
        self.lbl_artist.config(text=artist)
        if self._compact_mode:
            try:
                if self._compact_title and self._compact_title.winfo_exists():
                    self._compact_title.config(text=title)
                if self._compact_artist and self._compact_artist.winfo_exists():
                    self._compact_artist.config(text=artist)
            except tk.TclError:
                pass
        self.lbl_time.config(text=f"{self._fmt(self.song_pos)} / {self._fmt(self._duration)}")
        play_text = "⏸" if self._playing else "▶"
        self.btn_play.set_text(play_text)
        cp = getattr(self, "_compact_play", None)
        if cp is not None:
            try:
                if cp.winfo_exists():
                    cp.set_text(play_text)
            except tk.TclError:
                pass

        if not self._seeking:
            try:
                self.progress.config(to=max(self._duration, 1.0))
                self.progress.set(min(self.song_pos, self._duration or self.song_pos))
            except Exception:
                pass

        if self.lines:
            idx = LrcParser.find_current(self.lines, self.song_pos)
            
            # ★ 向上回溯，锁定主行，严格容差（< 0.6 秒）
            main_idx = idx
            if idx > 0:
                t_prev = self.lines[idx - 1][0]
                t_curr = self.lines[idx][0]
                if t_curr - t_prev < 0.6 and self.lines[idx-1][1] != self.lines[idx][1]:
                    main_idx = idx - 1

            if idx != self._last_idx:
                self._last_idx = idx
                self.cur_line_idx = main_idx
                main, trans, next_t, _ = self._get_current_texts()
                self.overlay.set_line(main, next_t, info.get("title", ""), trans)
            
            if 0 <= main_idx < len(self.lines):
                main, trans, next_t, t1 = self._get_current_texts()
                t0 = self.lines[main_idx][0]
                span = max(t1 - t0, 0.3)
                self.overlay.set_progress((self.song_pos - t0) / span)
                
        elif self._lyrics_source == "未找到":
            self.overlay.clear(info.get("title", ""), message=f"暂无歌词：{info.get('title', '')}")
        else:
            t = info.get("title", "")
            self.overlay.clear(t, message=f"{t}" if t else "")

        try:
            vis = self.overlay.is_visible()
            self.btn_lyrics.set_text("♪" if vis else "♩")
            if self._last_overlay_vis is None:
                self._last_overlay_vis = vis
            elif self._last_overlay_vis != vis:
                self._last_overlay_vis = vis
                self.cfg.OVERLAY_VISIBLE = vis
                self.cfg.save()
        except Exception:
                    pass        
        try:
            if self._ns_current_album and self.ns_list_wrap.winfo_ismapped():
                self._update_ns_now_playing()
                self._ns_sync_selection()
        except Exception:
            pass
        try:
            self._lib_sync_selection()
        except Exception:
            pass
        try:
            self._fav_sync_selection()
        except Exception:
            pass
        # 保险：若 HOST_MAC 刚就绪，检测到旧 list 格式立即迁移
        try:
            if (isinstance(getattr(self.cfg, "FAVORITES", None), list)
                    or isinstance(getattr(self.cfg, "PLAYLISTS", None), list)):
                self._migrate_legacy_fav_pl()
        except Exception:
            pass
                        
    @staticmethod
    def _fmt(sec):
        try:
            sec = int(float(sec))
        except (TypeError, ValueError):
            sec = 0
        return f"{sec // 60:02d}:{sec % 60:02d}"

    def _line_text(self):
        if 0 <= self.cur_line_idx < len(self.lines): return self.lines[self.cur_line_idx][1]
        return ""

    def _next_text(self):
        i = self.cur_line_idx + 1
        if 0 <= i < len(self.lines): return self.lines[i][1]
        return ""

    # ================= 音乐库（三列） =================
    def _lib_set_status(self, text):
        self.lbl_lib.config(text=text)

    def _lib_clear_artists(self):
        self.tree_artists.delete(*self.tree_artists.get_children())
        self._lib_artists.clear()

    def _lib_clear_albums(self):
        self.tree_albums.delete(*self.tree_albums.get_children())
        self._lib_albums.clear()

    def _lib_clear_tracks(self):
        self.tree_tracks.delete(*self.tree_tracks.get_children())
        self._lib_tracks.clear()

    def _make_icon(self, kind):
        if kind in self._icon_cache:
            return self._icon_cache[kind]
        if not HAS_PIL:
            self._icon_cache[kind] = None
            return None

        size = 18
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        accent = self.ACCENT
        dark = "#3D3D5C" if self.cfg.DARK_MODE else "#C0C0C8"

        if kind == "artist":
            d.ellipse((6, 1, 12, 10), fill=accent)
            d.ellipse((7, 2, 9, 6), fill="#FFFFFF")
            d.rectangle((8, 10, 10, 15), fill=dark)
            d.rectangle((6, 15, 12, 16), fill=dark)
        elif kind == "album":
            d.ellipse((0, 0, 17, 17), fill=dark)
            d.ellipse((4, 4, 13, 13), fill=accent)
            d.ellipse((7, 7, 10, 10), fill=self.BG)
            d.arc((1, 1, 16, 16), start=200, end=280, fill="#A0A0B0" if not self.cfg.DARK_MODE else "#7A7AA0", width=1)

        try:
            photo = ImageTk.PhotoImage(img)
        except Exception:
            photo = None
        self._icon_cache[kind] = photo
        return photo

    def _set_headers(self, level):
        if level == "artists":
            self.lbl_hdr_albums.config(text="")
            self.lbl_hdr_tracks.config(text="")
        elif level == "albums":
            self.lbl_hdr_albums.config(text="专辑")
            self.lbl_hdr_tracks.config(text="")
        elif level == "tracks":
            self.lbl_hdr_albums.config(text="专辑")
            self.lbl_hdr_tracks.config(text="歌曲")

        if level == "artists":
            self.col_c2.grid_remove()
            self.col_c3.grid_remove()
            self.cols_frame.columnconfigure(0, weight=1)
            self.cols_frame.columnconfigure(1, weight=0)
            self.cols_frame.columnconfigure(2, weight=0)
        elif level == "albums":
            self.col_c2.grid()
            self.col_c3.grid_remove()
            self.cols_frame.columnconfigure(0, weight=1)
            self.cols_frame.columnconfigure(1, weight=1)
            self.cols_frame.columnconfigure(2, weight=0)
        else:
            self.col_c2.grid()
            self.col_c3.grid()
            self.cols_frame.columnconfigure(0, weight=1)
            self.cols_frame.columnconfigure(1, weight=1)
            self.cols_frame.columnconfigure(2, weight=1)

    def _lib_show_artists(self):
        self._lib_clear_artists()
        self._lib_clear_albums()
        self._lib_clear_tracks()
        self._set_headers("artists")
        self._lib_set_status("加载艺人中…")
        token = self._lib_artist_token = self._lib_artist_token + 1

        def work():
            try:
                data = self.lms.browse_artists()
            except Exception as e:
                print(f"[音乐库] {e}")
                data = []
            self.panel.after(0, lambda: self._lib_fill_artists(data, token))

        threading.Thread(target=work, daemon=True).start()

    def _lib_fill_artists(self, artists, token):
        if token != self._lib_artist_token: return
        self._lib_clear_artists()
        for a in artists:
            aid, aname = a
            iid = f"ar{aid}"
            self.tree_artists.insert("", "end", iid=iid, text=aname, image=self._make_icon("artist"))
            self._lib_artists[iid] = (str(aid), aname)
        self._lib_set_status(f"共 {len(artists)} 位艺人 · 单击查看专辑")

    def _lib_load_albums(self, artist_id, artist_name):
        self._lib_clear_albums()
        self._lib_clear_tracks()
        self._set_headers("albums")
        self._lib_set_status(f"加载 {artist_name} 的专辑…")
        token = self._lib_album_token = self._lib_album_token + 1

        def work():
            try:
                data = self.lms.browse_albums(artist_id)
            except Exception as e:
                print(f"[音乐库] {e}")
                data = []
            self.panel.after(0, lambda: self._lib_fill_albums(data, artist_name, token))

        threading.Thread(target=work, daemon=True).start()

    def _lib_fill_albums(self, albums, artist_name, token):
        if token != self._lib_album_token: return
        self._lib_clear_albums()
        for al in albums:
            alid, alname = al
            iid = f"al{alid}"
            self.tree_albums.insert("", "end", iid=iid, text=alname, image=self._make_icon("album"))
            self._lib_albums[iid] = (str(alid), alname)
            
        self._lib_set_status(f"{artist_name} · {len(albums)} 张专辑 · 单击查看歌曲")

        if getattr(self, "_kb_focus_albums", False):
            self._kb_focus_albums = False
            if albums:
                self.tree_albums.selection_set(self.tree_albums.get_children()[0])
                self.tree_albums.focus(self.tree_albums.get_children()[0])
                self._kb_select_tracks = False
                self._on_album_selected()

    def _lib_load_tracks(self, album_id, album_name):
        self._lib_clear_tracks()
        self._set_headers("tracks")
        self._lib_set_status(f"加载 {album_name} 的歌曲…")
        token = self._lib_track_token = self._lib_track_token + 1

        def work():
            try:
                data = self.lms.browse_tracks(album_id)
            except Exception as e:
                print(f"[音乐库] {e}")
                data = []
            self.panel.after(0, lambda: self._lib_fill_tracks(data, album_name, token))

        threading.Thread(target=work, daemon=True).start()

    def _lib_fill_tracks(self, tracks, album_name, token):
        if token != self._lib_track_token: return
        self._lib_clear_tracks()
        for t in tracks:
            tid, tno, tname = t
            iid = f"tr{tid}"
            label = f"{tno}. {tname}" if tno else tname
            self.tree_tracks.insert("", "end", iid=iid, text=label)
            self._lib_tracks[iid] = (str(tid), tname)
            
        self._lib_set_status(f"{album_name} · {len(tracks)} 首 · 双击播放")

        if getattr(self, "_kb_select_tracks", False):
            self._kb_select_tracks = False
            if tracks:
                self.tree_tracks.selection_set(self.tree_tracks.get_children()[0])
                self.tree_tracks.focus(self.tree_tracks.get_children()[0])
                self.tree_tracks.focus_set()
        # 数据刷新后重置同步状态：让下一次 tick 重新同步到正在播放曲目
        self._lib_last_synced_track = None
        try:
            self.panel.after(60, self._lib_sync_selection)
        except tk.TclError:
            pass
        try:
            self.panel.after(30, lambda: self._redraw_hearts(
                self.tree_tracks, self.tree_tracks_hearts, self._lib_tracks))
        except tk.TclError:
            pass
                
    def _lib_sync_selection(self):
        """音乐库：当播放曲目属于当前显示的专辑时，让 tree_tracks 高亮对应歌曲。
           仅在 track_id 变化时写一次 selection，避免打断用户手动选择。"""
        # 仅音乐库标签页
        try:
            if self.nb.index(self.nb.select()) != 1:
                return
        except (tk.TclError, ValueError):
            return
        # 歌曲列必须有内容
        try:
            if not self.tree_tracks.get_children():
                return
        except tk.TclError:
            return

        ti = self.track_info or {}
        cur_album = (ti.get("album", "") or "").strip()
        cur_tid = str(ti.get("track_id") or "")
        if not cur_tid or not cur_album:
            return

        # 当前显示的专辑名（tree_albums 有选中时取它）
        try:
            sel = self.tree_albums.selection()
        except tk.TclError:
            return
        if not sel:
            return
        info = self._lib_albums.get(sel[0])
        if not info:
            return
        _alid, alname = info
        if not alname or cur_album != alname.strip():
            return

        # 该曲目是否在当前歌曲列表中
        iid = f"tr{cur_tid}"
        if iid not in self._lib_tracks:
            return

        # 同一 track_id 不重复写 selection
        if self._lib_last_synced_track == cur_tid:
            return
        self._lib_last_synced_track = cur_tid

        try:
            self._redraw_hearts(
                self.tree_tracks, self.tree_tracks_hearts, self._lib_tracks)
        except Exception:
            pass
        try:
            cur_sel = self.tree_tracks.selection()
            if cur_sel and cur_sel[0] == iid:
                return
            self.tree_tracks.selection_set(iid)
            self.tree_tracks.focus(iid)
            self.tree_tracks.see(iid)
        except tk.TclError:
            pass
        try:
            self._redraw_hearts(
                self.tree_tracks, self.tree_tracks_hearts, self._lib_tracks)
        except Exception:
            pass
                
    def _on_artist_selected(self, _e=None):
        sel = self.tree_artists.selection()
        if not sel: return
        info = self._lib_artists.get(sel[0])
        if not info: return
        aid, aname = info
        if not aid:
            self._lib_set_status(f"「{aname}」来自搜索结果，无独立 ID，无法浏览专辑")
            return
        self._lib_load_albums(aid, aname)

    def _on_album_selected(self, _e=None):
        sel = self.tree_albums.selection()
        if not sel: return
        info = self._lib_albums.get(sel[0])
        if not info: return
        alid, alname = info
        if not alid:
            self._lib_set_status(f"「{alname}」缺少 ID，无法加载歌曲")
            return
        self._lib_load_tracks(alid, alname)

    def _on_track_activate(self, _e=None):
        sel = self.tree_tracks.selection()
        if not sel: return
        info = self._lib_tracks.get(sel[0])
        if not info: return
        tid, tname = info
        self._lib_last_synced_track = str(tid)
        self._play_mode = "smart"      # ★ 恢复智能切歌
        self.lms.play_track(tid)
        self._lib_set_status(f"正在播放: {tname}")

    def _lib_search(self):
        q = self.ent_search.get().strip()
        if not q: return
        self._lib_set_status(f"搜索“{q}”…")

        def work():
            try:
                res = self.lms.search(q)
            except Exception as e:
                print(f"[音乐库] {e}")
                res = {"artists": [], "albums": [], "tracks": []}
            self.panel.after(0, lambda: self._lib_fill_search(res))

        threading.Thread(target=work, daemon=True).start()

    def _lib_fill_search(self, res):
        if isinstance(res, list):
            d = {"artists": [], "albums": [], "tracks": []}
            for item in res:
                if isinstance(item, dict) and "title" in item:
                    d["tracks"].append((item.get("id"), 0, item["title"], "", "", ""))
                elif isinstance(item, dict):
                    d["artists"].append((item.get("id"), item.get("name", str(item))))
            res = d
        if not isinstance(res, dict):
            self._lib_set_status("搜索结果格式异常")
            return

        artists_list = res.get("artists", []) or []
        albums_list = res.get("albums", []) or []
        tracks_list = res.get("tracks", []) or []

        self._lib_clear_artists()
        self._lib_clear_albums()
        self._lib_clear_tracks()

        track_albums = []
        song_artist_names = []
        seen_song_artist_names = set()

        for t in tracks_list:
            if isinstance(t, (tuple, list)) and len(t) >= 6:
                tid, tno, tname = t[0], t[1], t[2]
                artist, album = t[3] or "", t[4] or ""
                album_id = str(t[5] or "")
            elif isinstance(t, (tuple, list)) and len(t) >= 5:
                tid, tno, tname = t[0], t[1], t[2]
                artist, album = t[3] or "", t[4] or ""
                album_id = ""
            elif isinstance(t, (tuple, list)) and len(t) >= 3:
                tid, tno, tname = t[0], t[1], t[2]
                artist, album, album_id = "", "", ""
            else:
                continue

            label = f"{tno}. {tname}" if tno else tname
            iid = f"tr{tid}"
            self.tree_tracks.insert("", "end", iid=iid, text=label)
            self._lib_tracks[iid] = (str(tid), tname)

            if album and album_id:
                track_albums.append((album_id, album))
            if artist and artist not in seen_song_artist_names:
                seen_song_artist_names.add(artist)
                song_artist_names.append(artist)

        loaded_artist_names = set()
        for a in artists_list:
            if isinstance(a, (tuple, list)) and len(a) >= 2:
                aid, aname = a[0], a[1]
            else:
                aid, aname = a, str(a)
            if not aname: continue
            iid = f"ar{aid}"
            self.tree_artists.insert("", "end", iid=iid, text=aname, image=self._make_icon("artist"))
            self._lib_artists[iid] = (str(aid), aname)
            loaded_artist_names.add(aname)

        for aname in song_artist_names:
            if aname in loaded_artist_names: continue
            loaded_artist_names.add(aname)
            iid = f"arnoart_{aname}"
            self.tree_artists.insert("", "end", iid=iid, text=aname, image=self._make_icon("artist"))
            self._lib_artists[iid] = ("", aname)

        seen_album_keys = set()
        ordered_albums = []

        for al in albums_list:
            if isinstance(al, (tuple, list)) and len(al) >= 2:
                alid, alname = str(al[0] or ""), al[1]
            else:
                alid, alname = "", str(al)
            if not alname or not alid: continue
            key = (alid, alname)
            if key in seen_album_keys: continue
            seen_album_keys.add(key)
            ordered_albums.append((alid, alname))

        existing_album_names = {n for _, n in ordered_albums}
        for alid, alname in track_albums:
            if alname in existing_album_names: continue
            key = (alid, alname)
            if key in seen_album_keys: continue
            seen_album_keys.add(key)
            ordered_albums.append((alid, alname))
            existing_album_names.add(alname)

        for alid, alname in ordered_albums:
            iid = f"al{alid}"
            self.tree_albums.insert("", "end", iid=iid, text=alname, image=self._make_icon("album"))
            self._lib_albums[iid] = (alid, alname)

        if tracks_list:
            self._set_headers("tracks")
        else:
            self._set_headers("artists")
            self._lib_clear_albums()

        n = (len(loaded_artist_names) + (len(ordered_albums) if tracks_list else 0) + len(tracks_list))
        self._lib_set_status(f"搜索到 {n} 项")

    def _lib_refresh(self):
        self._lib_show_artists()

    # ================= 收藏 / 歌单（按设备 MAC 隔离）=================
    def _get_current_host_mac(self):
        """当前连接的达菲主机 MAC（小写规范化）；未就绪时返回 ''。"""
        return (getattr(self.cfg, "HOST_MAC", "") or "").strip().lower()

    def _get_favorites_for_current(self):
        """返回当前主机 MAC 对应的收藏列表（直接引用内部 list）。"""
        mac = self._get_current_host_mac()
        data = getattr(self.cfg, "FAVORITES", {}) or {}
        if not isinstance(data, dict):
            data = {}
            self.cfg.FAVORITES = data
        if mac not in data:
            data[mac] = []
            self.cfg.FAVORITES = data
        return data[mac]

    def _set_favorites_for_current(self, favs):
        mac = self._get_current_host_mac()
        data = getattr(self.cfg, "FAVORITES", {}) or {}
        if not isinstance(data, dict):
            data = {}
        data[mac] = list(favs or [])
        self.cfg.FAVORITES = data
        try:
            self.cfg.save()
        except Exception:
            pass

    def _get_playlists_for_current(self):
        """返回当前主机 MAC 对应的歌单列表（直接引用内部 list）。"""
        mac = self._get_current_host_mac()
        data = getattr(self.cfg, "PLAYLISTS", {}) or {}
        if not isinstance(data, dict):
            data = {}
            self.cfg.PLAYLISTS = data
        if mac not in data:
            data[mac] = []
            self.cfg.PLAYLISTS = data
        return data[mac]

    def _set_playlists_for_current(self, pls):
        mac = self._get_current_host_mac()
        data = getattr(self.cfg, "PLAYLISTS", {}) or {}
        if not isinstance(data, dict):
            data = {}
        data[mac] = list(pls or [])
        self.cfg.PLAYLISTS = data
        try:
            self.cfg.save()
        except Exception:
            pass

    def _migrate_legacy_fav_pl(self):
        """旧版 list 格式 → 按当前 MAC 分组的 dict。
           若当前 MAC 尚未就绪则直接返回，后续会在 _ui_tick / reconnect 中再试。"""
        mac = self._get_current_host_mac()
        if not mac:
            return
        changed = False

        fav = getattr(self.cfg, "FAVORITES", None)
        if isinstance(fav, list):
            if fav:
                self.cfg.FAVORITES = {mac: list(fav)}
                print(f"[收藏] 迁移旧 list 数据 → MAC {mac} ({len(fav)} 首)")
            else:
                self.cfg.FAVORITES = {}
            changed = True

        pl = getattr(self.cfg, "PLAYLISTS", None)
        if isinstance(pl, list):
            if pl:
                self.cfg.PLAYLISTS = {mac: list(pl)}
                print(f"[歌单] 迁移旧 list 数据 → MAC {mac} ({len(pl)} 个)")
            else:
                self.cfg.PLAYLISTS = {}
            changed = True

        if changed:
            try:
                self.cfg.save()
            except Exception:
                pass

    # ================= 收藏 / 歌单 =================
    def _is_favorite(self, track_id):
        tid = str(track_id)
        for f in self._get_favorites_for_current():
            if isinstance(f, dict) and str(f.get("id")) == tid:
                return True
        return False

    def _toggle_favorite(self, track_id, title="", artist="", album=""):
        tid = str(track_id)
        favs = list(self._get_favorites_for_current())
        for i, f in enumerate(favs):
            if isinstance(f, dict) and str(f.get("id")) == tid:
                del favs[i]
                self._set_favorites_for_current(favs)
                self._after_favorite_changed()
                return
        favs.append({"id": tid, "title": title or "",
                     "artist": artist or "", "album": album or ""})
        self._set_favorites_for_current(favs)
        self._after_favorite_changed()

    def _after_favorite_changed(self):
        """收藏变化后刷新歌名右侧的红心（Canvas 重绘）。"""
        try:
            self._redraw_hearts(self.tree_tracks,
                                self.tree_tracks_hearts, self._lib_tracks)
        except Exception:
            pass
        try:
            self._redraw_hearts(self.ns_tree,
                                self.ns_tree_hearts, self._ns_tracks)
        except Exception:
            pass
        try:
            if self.nb.index(self.nb.select()) == 3:
                self._fav_refresh()
        except Exception:
            pass
        
    def _get_heart_photo(self, size=13):
        """手绘红心：修正 y 映射范围（原来裁剪了上半部分 → 显示为倒三角），
           PIL 超采样 + LANCZOS 抗锯齿。"""
        if size in self._heart_cache:
            return self._heart_cache[size]
        if not HAS_PIL:
            return None
        try:
            ss = 4
            W = size * ss
            img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            points = []
            N = 240
            for i in range(N):
                t = i * 2 * math.pi / N
                x = 16 * math.sin(t) ** 3
                y = (13 * math.cos(t) - 5 * math.cos(2 * t)
                     - 2 * math.cos(3 * t) - math.cos(4 * t))
                # 实际 x ∈ [-16, 16]，y ∈ [-17, 11.924]
                # 早期代码假设 y_max=5 → 上半部分被裁出图片 → 显示成倒三角
                px = W * 0.05 + (x + 16) / 32 * W * 0.90
                py = W * 0.05 + (11.924 - y) / 28.924 * W * 0.90
                points.append((px, py))
            d.polygon(points, fill="#FF3B5C")
            small = img.resize((size, size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(small)
            self._heart_cache[size] = photo
            return photo
        except Exception:
            return None

    def _redraw_hearts(self, tree, canvas, tracks_dict):
        """右侧 Canvas：选中行绘制高亮底色 + 收藏行绘制手绘红心。"""
        try:
            canvas.delete("all")
        except tk.TclError:
            return
        if not HAS_PIL:
            return
        heart = self._get_heart_photo(13)
        if heart is None:
            return
        try:
            cw = max(canvas.winfo_width(), 18)
            sel = set(tree.selection())
            # ★ 用与 Treeview 选中色完全一致的颜色
            sel_color = self._sel_highlight_color()

            for iid in tree.get_children():
                bbox = tree.bbox(iid, "#0")
                if not bbox:
                    continue
                _x, y, _w, h = bbox

                # ① 选中行 → 先铺满高亮色（红心区与 Treeview 高亮无缝衔接）
                if iid in sel:
                    try:
                        canvas.create_rectangle(
                            0, y, cw, y + h,
                            fill=sel_color, outline="")
                    except tk.TclError:
                        pass

                # ② 收藏行 → 再绘红心
                info = tracks_dict.get(iid)
                if not info:
                    continue
                try:
                    tid = str(info[0])
                except Exception:
                    continue
                if self._is_favorite(tid):
                    try:
                        canvas.create_image(cw // 2, y + h // 2, image=heart)
                    except tk.TclError:
                        pass
            canvas._heart_ref = heart
        except tk.TclError:
            pass

    def _apply_fav_mark(self, tree, iid, info, keep_prefix=False):
        """红心由右侧 Canvas 绘制，这里只触发重绘（不再修改行 image）。"""
        try:
            if tree is self.tree_tracks:
                self._redraw_hearts(self.tree_tracks,
                                    self.tree_tracks_hearts, self._lib_tracks)
            elif tree is self.ns_tree:
                self._redraw_hearts(self.ns_tree,
                                    self.ns_tree_hearts, self._ns_tracks)
        except Exception:
            pass

    # ---------- 右键菜单 ----------
    def _show_track_menu(self, event, track_id, title="", artist="", album=""):
        if not track_id:
            return
        tid = str(track_id)
        is_fav = self._is_favorite(tid)

        menu = tk.Menu(self.panel, tearoff=0,
                       bg=self.CARD, fg=self.FG,
                       activebackground=self.ACCENT,
                       activeforeground="#FFFFFF",
                       font=("Microsoft YaHei UI", 9))

        fav_label = "❌ 取消收藏" if is_fav else "♥ 添加收藏"
        menu.add_command(
            label=fav_label,
            command=lambda: self._toggle_favorite(tid, title, artist, album))

        menu.add_separator()

        sub = tk.Menu(menu, tearoff=0,
                      bg=self.CARD, fg=self.FG,
                      activebackground=self.ACCENT,
                      activeforeground="#FFFFFF",
                      font=("Microsoft YaHei UI", 9))

        playlists = self._get_playlists_for_current()
        if playlists:
            for pl in playlists:
                if not isinstance(pl, dict):
                    continue
                pl_name = pl.get("name", "")
                if not pl_name:
                    continue
                sub.add_command(
                    label=pl_name,
                    command=lambda pn=pl_name: self._add_to_playlist(
                        pn, tid, title, artist, album))
        else:
            sub.add_command(label="(暂无歌单)", state="disabled")

        sub.add_separator()
        sub.add_command(
            label="+ 新建歌单…",
            command=lambda: self._add_to_new_playlist(
                tid, title, artist, album))

        menu.add_cascade(label="+ 添加到歌单", menu=sub)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _on_lib_track_right_click(self, event):
        iid = self.tree_tracks.identify_row(event.y)
        if not iid:
            return
        self.tree_tracks.selection_set(iid)
        info = self._lib_tracks.get(iid)
        if not info:
            return
        tid, title = info
        artist, album = "", ""
        sel_alb = self.tree_albums.selection()
        if sel_alb:
            a = self._lib_albums.get(sel_alb[0])
            if a:
                album = a[1]
        sel_art = self.tree_artists.selection()
        if sel_art:
            a = self._lib_artists.get(sel_art[0])
            if a:
                artist = a[1]
        self._show_track_menu(event, tid, title, artist, album)

    def _on_ns_track_right_click(self, event):
        iid = self.ns_tree.identify_row(event.y)
        if not iid:
            return
        self.ns_tree.selection_set(iid)
        info = self._ns_tracks.get(iid)
        if not info:
            return
        tid, title = info
        artist, album = "", ""
        if self._ns_current_album:
            _, album, artist, _ = self._ns_current_album
        self._show_track_menu(event, tid, title, artist, album)

    # ---------- 收藏页 ----------
    def _fav_ensure_loaded(self):
        self._fav_refresh()

    def _fav_refresh(self):
        self._fav_last_synced_track = None
        self._fav_pending_track = None        
        try:
            self.fav_tree.delete(*self.fav_tree.get_children())
        except Exception:
            pass
        self._fav_tracks.clear()
        favs = favs = self._get_favorites_for_current()
        for idx, f in enumerate(favs):
            if not isinstance(f, dict):
                continue
            tid = str(f.get("id", "") or "")
            if not tid:
                continue
            title = f.get("title", "") or ""
            artist = f.get("artist", "") or ""
            album = f.get("album", "") or ""
            disp = title
            if artist:
                disp = f"{title} - {artist}"
            iid = f"fav{tid}"
            if iid in self._fav_tracks:
                iid = f"fav{tid}_{idx}"
            try:
                self.fav_tree.insert("", "end", iid=iid, text=disp)
                self._fav_tracks[iid] = (tid, title, artist, album)
            except Exception:
                pass
        try:
            self.lbl_fav_title.config(
                text=f"收藏 · {len(self._fav_tracks)} 首")
        except Exception:
            pass
        try:
            self.panel.after(50, self._fav_sync_selection)
        except tk.TclError:
            pass        

    def _on_fav_activate(self, _e=None):
        sel = self.fav_tree.selection()
        if not sel:
            return "break"
        info = self._fav_tracks.get(sel[0])
        if not info:
            return "break"
        tid = str(info[0])
        # ★ 标记等待 poll 更新 track_info，防止 tick 用旧 track_id 覆盖选中
        self._fav_pending_track = tid
        self._fav_last_synced_track = tid
        
        # ★ 把整张收藏列表作为 LMS 播放队列，从当前曲目位置开始播放
        track_ids = []
        start_index = 0
        try:
            for iid in self.fav_tree.get_children():
                fi = self._fav_tracks.get(iid)
                if not fi:
                    continue
                fid = str(fi[0])
                track_ids.append(fid)
                if fid == tid:
                    start_index = len(track_ids) - 1
        except tk.TclError:
            pass

        if not track_ids:
            track_ids = [tid]
            start_index = 0

        # ★ 切歌模式 = 严格队列顺序
        self._play_mode = "queue"

        def work():
            try:
                self.lms.play_playlist(track_ids, start_index)
            except Exception:
                traceback.print_exc()

        threading.Thread(target=work, daemon=True).start()
        return "break"

    def _on_fav_right_click(self, event):
        iid = self.fav_tree.identify_row(event.y)
        if not iid:
            return
        self.fav_tree.selection_set(iid)
        info = self._fav_tracks.get(iid)
        if info:
            tid, title, artist, album = info
            self._show_track_menu(event, tid, title, artist, album)

    # ---------- 歌单页 ----------
    def _pl_ensure_loaded(self):
        self._pl_refresh()

    def _pl_refresh(self):
        cur_sel = self.pl_tree.selection()
        cur_name = cur_sel[0] if cur_sel else None

        try:
            self.pl_tree.delete(*self.pl_tree.get_children())
        except Exception:
            pass
        try:
            self.pl_tracks_tree.delete(*self.pl_tracks_tree.get_children())
        except Exception:
            pass
        self._pl_tracks.clear()

        # ★ 防御性合并：同名歌单合并为一条，tracks 去重
        raw = list(self._get_playlists_for_current())
        merged = {}
        order = []
        for pl in raw:
            if not isinstance(pl, dict):
                continue
            name = str(pl.get("name", "") or "").strip()
            if not name:
                continue
            if name not in merged:
                merged[name] = {"name": name, "tracks": []}
                order.append(name)
            existing_ids = {str(t.get("id"))
                            for t in merged[name]["tracks"]
                            if isinstance(t, dict)}
            for t in (pl.get("tracks", []) or []):
                if not isinstance(t, dict):
                    continue
                tid = str(t.get("id", "") or "")
                if tid and tid not in existing_ids:
                    merged[name]["tracks"].append(t)
                    existing_ids.add(tid)

        clean = [merged[n] for n in order]

        # 若合并后结构有变化 → 写回配置（自动修复重复数据）
        if len(clean) != len(raw):
            try:
                self._set_playlists_for_current(clean)
                print(f"[歌单] 检测到 {len(raw) - len(clean)} 条同名重复，"
                      f"已自动合并")
            except Exception:
                pass

        for pl in clean:
            name = pl["name"]
            count = len(pl["tracks"])
            try:
                self.pl_tree.insert("", "end", iid=name,
                                    text=f"{name} ({count})")
            except Exception:
                pass

        try:
            self.lbl_pl_title.config(text=f"歌单 · {len(clean)} 个")
        except Exception:
            pass

        if cur_name and cur_name in self.pl_tree.get_children():
            try:
                self.pl_tree.selection_set(cur_name)
                self.pl_tree.focus(cur_name)
                self._pl_load_tracks(cur_name)
            except Exception:
                pass

    def _on_pl_selected(self, _e=None):
        sel = self.pl_tree.selection()
        if not sel:
            try:
                self.pl_tracks_tree.delete(
                    *self.pl_tracks_tree.get_children())
            except Exception:
                pass
            self._pl_tracks.clear()
            return
        self._pl_load_tracks(sel[0])

    def _on_pl_double(self, _e=None):
        sel = self.pl_tree.selection()
        if not sel:
            return "break"
        self._play_mode = "smart"
        self._play_playlist_by_name(sel[0], 0)
        return "break"

    def _on_pl_enter(self, _e=None):
        return self._on_pl_double()
    
    def _on_pl_track_double(self, _e=None):
        sel = self.pl_tracks_tree.selection()
        if not sel:
            return "break"
        info = self._pl_tracks.get(sel[0])
        if not info:
            return "break"
        idx = info[4]
        cur_pl = self.pl_tree.selection()
        if not cur_pl:
            return "break"
        self._play_mode = "smart"      # ★ 歌单走智能切歌（保留原行为）
        self._play_playlist_by_name(cur_pl[0], idx)
        return "break"
    
    def _play_playlist_by_name(self, pl_name, start_index=0):
        playlists = self._get_playlists_for_current()
        for pl in playlists:
            if not isinstance(pl, dict) or pl.get("name") != pl_name:
                continue
            tracks = pl.get("tracks", []) or []
            track_ids = [str(t.get("id")) for t in tracks if t.get("id")]
            if not track_ids:
                return

            def work():
                try:
                    self.lms.play_playlist(track_ids, start_index)
                except Exception:
                    traceback.print_exc()

            threading.Thread(target=work, daemon=True).start()
            return

    def _on_pl_right_click(self, event):
        """左列歌单右键"""
        iid = self.pl_tree.identify_row(event.y)
        if not iid:
            return
        self.pl_tree.selection_set(iid)
        menu = tk.Menu(self.panel, tearoff=0,
                       bg=self.CARD, fg=self.FG,
                       activebackground=self.ACCENT,
                       activeforeground="#FFFFFF",
                       font=("Microsoft YaHei UI", 9))
        menu.add_command(
            label="▶ 开始播放",
            command=lambda: self._play_playlist_by_name(iid, 0))
        menu.add_separator()
        menu.add_command(
            label="🗑 删除歌单",
            command=lambda: self._delete_playlist(iid))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _on_pl_track_right_click(self, event):
        """右列歌曲右键"""
        iid = self.pl_tracks_tree.identify_row(event.y)
        if not iid:
            return
        self.pl_tracks_tree.selection_set(iid)
        info = self._pl_tracks.get(iid)
        if not info:
            return
        tid, title, artist, album, _idx = info
        is_fav = self._is_favorite(tid)
        menu = tk.Menu(self.panel, tearoff=0,
                       bg=self.CARD, fg=self.FG,
                       activebackground=self.ACCENT,
                       activeforeground="#FFFFFF",
                       font=("Microsoft YaHei UI", 9))
        menu.add_command(
            label="❌ 取消收藏" if is_fav else "♥ 添加收藏",
            command=lambda: self._toggle_favorite(tid, title, artist, album))
        menu.add_separator()
        menu.add_command(
            label="🗑 从此歌单移除",
            command=lambda: self._remove_track_from_current_playlist(iid))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass   

    def _delete_playlist(self, pl_name):
        if not self._ask_yesno("删除歌单",
                               f"确定删除歌单「{pl_name}」吗？",
                               danger=True):
            return
        playlists = list(self._get_playlists_for_current())
        new_pl = [pl for pl in playlists if pl.get("name") != pl_name]
        self._set_playlists_for_current(new_pl)
        self._pl_refresh()


    def _remove_track_from_current_playlist(self, iid):
        info = self._pl_tracks.get(iid)
        if not info:
            return
        tid = info[0]
        cur_pl = self.pl_tree.selection()
        if not cur_pl:
            return
        pl_name = cur_pl[0]
        playlists = list(self._get_playlists_for_current())
        for pl in playlists:
            if pl.get("name") == pl_name:
                pl["tracks"] = [t for t in (pl.get("tracks", []) or [])
                                if str(t.get("id")) != str(tid)]
                break
        self._set_playlists_for_current(playlists)
        self._pl_refresh()

    # ---------- 添加歌单 ----------
    def _add_to_playlist(self, pl_name, track_id, title="", artist="", album=""):
        pl_name = str(pl_name or "").strip()
        if not pl_name:
            return
        tid = str(track_id)
        playlists = list(self._get_playlists_for_current())
        for pl in playlists:
            if not isinstance(pl, dict):
                continue
            # ★ 强制字符串 + strip 比较，避免任何类型/空白差异导致漏匹配
            if str(pl.get("name", "")).strip() == pl_name:
                tracks = list(pl.get("tracks", []) or [])
                for t in tracks:
                    if str(t.get("id")) == tid:
                        try:
                            self._lib_set_status(
                                f"「{title or tid}」已在歌单「{pl_name}」中")
                        except Exception:
                            pass
                        return
                tracks.append({"id": tid, "title": title or "",
                               "artist": artist or "", "album": album or ""})
                pl["tracks"] = tracks
                self._set_playlists_for_current(playlists)
                try:
                    if self.nb.index(self.nb.select()) == 4:
                        self._pl_refresh()
                except Exception:
                    pass
                return
        self._add_to_playlist_new(pl_name, tid, title, artist, album)
        self._add_to_playlist_new(pl_name, tid, title, artist, album)

    def _add_to_playlist_new(self, pl_name, track_id, title="", artist="", album=""):
        """「新建歌单」入口。防御：同名已存在时合并到已有歌单，绝不再追加新 dict。"""
        pl_name = str(pl_name or "").strip()
        if not pl_name:
            return
        tid = str(track_id)
        playlists = list(self._get_playlists_for_current())

        # ★ 同名检查：已存在则合并 tracks
        for pl in playlists:
            if not isinstance(pl, dict):
                continue
            if str(pl.get("name", "")).strip() == pl_name:
                tracks = list(pl.get("tracks", []) or [])
                for t in tracks:
                    if isinstance(t, dict) and str(t.get("id")) == tid:
                        return
                tracks.append({"id": tid, "title": title or "",
                               "artist": artist or "", "album": album or ""})
                pl["tracks"] = tracks
                self._set_playlists_for_current(playlists)
                return

        # 确实不存在才真正新建
        playlists.append({
            "name": pl_name,
            "tracks": [{"id": tid, "title": title or "",
                        "artist": artist or "", "album": album or ""}],
        })
        self._set_playlists_for_current(playlists)
    def _ask_yesno(self, title, message, danger=False):
        """自定义确认对话框，适配深浅主题与标题栏。返回 True/False。"""
        dlg = tk.Toplevel(self.panel)
        dlg.title(title)
        dlg.configure(bg=self.BG)
        dlg.resizable(False, False)
        dlg.transient(self.panel)
        dlg.grab_set()

        try:
            dlg.iconbitmap(_resource_path("icon.ico"))
        except Exception:
            pass

        # 按最长行估算宽度，按行数估算高度
        try:
            f = tkfont.Font(family="Microsoft YaHei UI", size=10)
            max_w = 0
            lines = message.split("\n")
            for line in lines:
                max_w = max(max_w, f.measure(line))
            line_h = f.metrics("linespace")
            n = max(1, len(lines))
            W = max(280, min(520, max_w + 80))
            H = max(110, min(220, 130 + n * line_h))
        except Exception:
            W, H = 320, 140

        dlg.update_idletasks()
        px = self.panel.winfo_rootx() + (self.panel.winfo_width() - W) // 2
        py = self.panel.winfo_rooty() + (self.panel.winfo_height() - H) // 2
        dlg.geometry(f"{W}x{H}+{px}+{py}")

        try:
            if self.cfg.DARK_MODE:
                apply_dark_titlebar(dlg, dark=True, bg_color=self.BG)
            else:
                apply_dark_titlebar(dlg, dark=False)
        except Exception:
            pass

        result = {"ok": False}

        tk.Label(dlg, text=message, bg=self.BG, fg=self.FG,
                 font=("Microsoft YaHei UI", 10),
                 justify="left", wraplength=W - 48,
                 anchor="w").pack(fill="x", padx=24, pady=(20, 14))

        btn_row = tk.Frame(dlg, bg=self.BG)
        btn_row.pack(fill="x", side="bottom", padx=24, pady=(0, 16))

        def on_ok():
            result["ok"] = True
            try:
                dlg.destroy()
            except Exception:
                pass

        def on_cancel():
            result["ok"] = False
            try:
                dlg.destroy()
            except Exception:
                pass

        ok_bg = "#E04040" if danger else self.ACCENT
        ok_hover = "#FF6B6B" if danger else self.HOVER_ACCENT

        tk.Button(btn_row, text="确定", command=on_ok,
                  bg=ok_bg, fg="#FFFFFF",
                  activebackground=ok_hover,
                  activeforeground="#FFFFFF",
                  relief="flat",
                  font=("Microsoft YaHei UI", 10, "bold"),
                  padx=22, pady=5, cursor="hand2",
                  highlightthickness=0, bd=0).pack(side="right", padx=(8, 0))

        tk.Button(btn_row, text="取消", command=on_cancel,
                  bg=self.CARD, fg=self.FG,
                  activebackground=self.SEL_BG,
                  activeforeground=self.FG,
                  relief="flat",
                  font=("Microsoft YaHei UI", 10),
                  padx=22, pady=5, cursor="hand2",
                  highlightthickness=0, bd=0).pack(side="right")

        dlg.bind("<Return>", lambda _e: on_ok())
        dlg.bind("<Escape>", lambda _e: on_cancel())
        dlg.protocol("WM_DELETE_WINDOW", on_cancel)

        try:
            self.panel.wait_window(dlg)
        except Exception:
            pass
        return result["ok"]
        
    def _ask_playlist_name(self, initial=""):
        """自定义新建歌单对话框，适配深浅主题与标题栏。返回名称或 None。"""
        dlg = tk.Toplevel(self.panel)
        dlg.title("新建歌单")
        dlg.configure(bg=self.BG)
        dlg.resizable(False, False)
        dlg.transient(self.panel)
        dlg.grab_set()

        try:
            dlg.iconbitmap(_resource_path("icon.ico"))
        except Exception:
            pass

        W, H = 320, 140
        dlg.update_idletasks()
        px = self.panel.winfo_rootx() + (self.panel.winfo_width() - W) // 2
        py = self.panel.winfo_rooty() + (self.panel.winfo_height() - H) // 2
        dlg.geometry(f"{W}x{H}+{px}+{py}")

        try:
            if self.cfg.DARK_MODE:
                apply_dark_titlebar(dlg, dark=True, bg_color=self.BG)
            else:
                apply_dark_titlebar(dlg, dark=False)
        except Exception:
            pass

        result = {"name": None}

        tk.Label(dlg, text="新建歌单", bg=self.BG, fg=self.FG,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(pady=(16, 10))

        # ★ 已删除 "请输入歌单名称：" 提示语
        ent = tk.Entry(dlg, bg=self.FIELD, fg=self.FG,
                       insertbackground=self.FG, relief="flat",
                       font=("Microsoft YaHei UI", 10),
                       highlightthickness=1,
                       highlightbackground=self.SEL_BG,
                       highlightcolor=self.ACCENT)
        # ★ ipady 6→3（变矮），padx 24→32（变窄），pady 收紧
        ent.pack(fill="x", ipady=3, padx=32, pady=(0, 14))
        ent.insert(0, initial or "")
        ent.focus_set()
        ent.select_range(0, "end")

        btn_row = tk.Frame(dlg, bg=self.BG)
        btn_row.pack(fill="x", padx=32, pady=(0, 14))

        def on_cancel(*_a):
            result["name"] = None
            try:
                dlg.destroy()
            except Exception:
                pass

        def on_ok(*_a):
            name = ent.get().strip()
            if not name:
                return
            result["name"] = name
            try:
                dlg.destroy()
            except Exception:
                pass

        btn_ok = tk.Button(btn_row, text="确定", command=on_ok,
                           bg=self.ACCENT, fg="#FFFFFF",
                           activebackground=self.HOVER_ACCENT,
                           activeforeground="#FFFFFF",
                           relief="flat",
                           font=("Microsoft YaHei UI", 10, "bold"),
                           padx=22, pady=5, cursor="hand2",
                           highlightthickness=0, bd=0)
        btn_ok.pack(side="right", padx=(8, 0))

        btn_cancel = tk.Button(btn_row, text="取消", command=on_cancel,
                               bg=self.CARD, fg=self.FG,
                               activebackground=self.SEL_BG,
                               activeforeground=self.FG,
                               relief="flat",
                               font=("Microsoft YaHei UI", 10),
                               padx=22, pady=5, cursor="hand2",
                               highlightthickness=0, bd=0)
        btn_cancel.pack(side="right")

        ent.bind("<Return>", on_ok)
        ent.bind("<Escape>", on_cancel)
        dlg.bind("<Escape>", on_cancel)

        try:
            self.panel.wait_window(dlg)
        except Exception:
            pass
        return result["name"]
    
    def _add_to_new_playlist(self, track_id, title="", artist="", album=""):
        name = self._ask_playlist_name()
        if not name:
            return
        self._add_to_playlist(name, track_id, title, artist, album)

    # ================= 关闭 =================
    def close(self):
        self._stop = True
        self._cover_cache.clear()
        self._cover_photo = None
        self._icon_cache.clear()

    def _wait_for_host_down(self, ip, ports=(80, 9000), timeout=30, interval=1.0):
        import socket
        start = time.time()
        attempt = 0
        while time.time() - start < timeout:
            attempt += 1
            all_down = True
            for port in ports:
                try:
                    with socket.create_connection((ip, port), timeout=1.0):
                        all_down = False
                        break
                except OSError:
                    pass
            if all_down:
                print(f"[关机] 主机 {ip} 已不可达（第 {attempt} 次检测），确认关机完成")
                return True
            time.sleep(interval)
        print(f"[关机] 超时 {timeout}s，主机 {ip} 仍在线")
        return False
    
    # ================= 关机 =================
    def _shutdown_daphile(self):
        confirm = self._ask_yesno(
            "确认关机",
            "确定要关闭达菲设备吗？\n\n关机后需要手动开机或远程唤醒。",
            danger=True)
        if not confirm:
            return

        self.btn_shutdown.set_state(False)
        self.btn_shutdown.set_text("正在关机…")
        self._lib_set_status("正在向达菲发送关机指令…")
        host = self.lms.host

        def work():
            ok = self.lms.shutdown_daphile()
            if not ok:
                def fail():
                    self._lib_set_status("关机指令发送失败，请检查网络")
                    self.btn_shutdown.set_text("⏻")
                    self.btn_shutdown.set_state(True)
                self.panel.after(0, fail)
                return

            def sent():
                self._lib_set_status("关机指令已发送，等待设备断开…")
                self.btn_shutdown.set_text("…")
            self.panel.after(0, sent)

            time.sleep(2)
            down = self._wait_for_host_down(host, timeout=30)

            def apply():
                if down:
                    self._lib_set_status("设备已关机，即将返回连接界面…")
                    self.panel.after(500, self._after_shutdown)
                else:
                    self._lib_set_status("等待超时，设备可能仍在运行，请手动确认")
                    self.btn_shutdown.set_text("⏻")
                    self.btn_shutdown.set_state(True)
            self.panel.after(0, apply)

        threading.Thread(target=work, daemon=True).start()

    def _after_shutdown(self):
        if callable(self.on_daphile_shutdown):
            try:
                self.on_daphile_shutdown()
            except Exception:
                traceback.print_exc()

    def reconnect(self, lms, fetcher, player_name, server_label):
        self.lms = lms
        self.fetcher = fetcher
        self.player_name = player_name or ""
        self.lbl_server.config(text=server_label)

        self._current_cover_id = ""
        self._cover_cache.clear()
        self._cover_photo = None
        self.lines = []
        self.cur_line_idx = -1
        self._last_idx = -1
        self._lyrics_source = ""
        self.track_info = {}

        self._ns_loaded = False
        self._ns_current_album = None
        self._ns_cover_cache.clear()
        self._ns_album_cover_cache.clear()
        self._ns_first_track_cache.clear()
        self._ns_grid_panel_h = None
                        
        try:
            if self.nb.index(self.nb.select()) == 2:
                self._ns_view = "grid"
                if self.ns_list_wrap.winfo_ismapped():
                    self._ns_back_to_grid()
                self._ns_refresh()
        except Exception:
            pass
        
        try:
            self.btn_shutdown.set_text("⏻")
            self.btn_shutdown.set_state(True)
        except Exception:
            pass

        try:
            self.panel.deiconify()
            self.panel.lift()
        except tk.TclError:
            pass

        try:
            if getattr(self.cfg, "OVERLAY_VISIBLE", True):
                self.overlay.show()
                self.btn_lyrics.set_text("♪")
            else:
                self.overlay.hide()
                self.btn_lyrics.set_text("♩")
        except Exception:
            pass
        try:
            self._migrate_legacy_fav_pl()
        except Exception:
            traceback.print_exc()
                
    def _get_circle_photo(self, diameter, color, bg=None):
        """Pillow 超采样生成抗锯齿圆，按 (直径, 颜色, 背景) 缓存。"""
        if not hasattr(self, "_circle_cache"):
            self._circle_cache = {}
        bg_color = bg if bg else self.BG
        key = (diameter, color, bg_color)
        if key in self._circle_cache:
            return self._circle_cache[key]
        if not HAS_PIL:
            return None
        try:
            ss = 4
            big = Image.new("RGB", (diameter * ss, diameter * ss), bg_color)
            bd = ImageDraw.Draw(big)
            bd.ellipse((0, 0, diameter * ss - 1, diameter * ss - 1), fill=color)
            small = big.resize((diameter, diameter), Image.LANCZOS)
            photo = ImageTk.PhotoImage(small)
            self._circle_cache[key] = photo
            return photo
        except Exception:
            return None

    def _get_toggle_photo(self, width, height, is_on, is_hover):
        """Pillow 超采样生成抗锯齿药丸开关图，按 (尺寸, 状态) 缓存。"""
        if not hasattr(self, "_toggle_cache"):
            self._toggle_cache = {}
        key = (width, height, is_on, is_hover)
        if key in self._toggle_cache:
            return self._toggle_cache[key]
        if not HAS_PIL:
            return None
        try:
            ss = 4
            W = width * ss
            H = height * ss
            if is_on:
                track = self.HOVER_ACCENT if is_hover else self.ACCENT
                knob = "#FFFFFF"
            else:
                track = self.HOVER_BG if is_hover else ("#D0D0D8" if not self.cfg.DARK_MODE else "#3D3D5C")
                knob = self.ACCENT

            img = Image.new("RGB", (W, H), self.CARD)
            d = ImageDraw.Draw(img)

            r = H // 2
            d.rectangle((r, 0, W - r, H), fill=track)
            d.ellipse((0, 0, H, H), fill=track)
            d.ellipse((W - H, 0, W, H), fill=track)

            pad = 3 * ss
            knob_d = H - pad * 2
            cy = H // 2
            if is_on:
                cx = W - pad - knob_d // 2
            else:
                cx = pad + knob_d // 2
            d.ellipse((cx - knob_d // 2, cy - knob_d // 2,
                       cx + knob_d // 2, cy + knob_d // 2),
                      fill=knob)

            small = img.resize((width, height), Image.LANCZOS)
            photo = ImageTk.PhotoImage(small)
            self._toggle_cache[key] = photo
            return photo
        except Exception:
            return None
        
    # ================= 美化控件工厂 =================
    def _make_round_btn(self, parent, text, cmd, width=112, height=22, radius=8, bg=None, btn_bg=None, font_size=9):
        canvas_bg = bg if bg is not None else self.BG
        button_bg = btn_bg if btn_bg is not None else self.CARD
        
        canvas = tk.Canvas(parent, width=width, height=height,
                           bg=canvas_bg, highlightthickness=0, bd=0)
        state = {"text": text, "hover": False, "enabled": True}

        def redraw():
            canvas.delete("all")
            if state["hover"]:
                if button_bg == self.ACCENT:
                    bg_color = self.HOVER_ACCENT
                elif button_bg == self.CARD:
                    bg_color = self.HOVER_BG
                else:
                    bg_color = self.SEL_BG
            else:
                bg_color = button_bg
            fg_color = self.FG if state["enabled"] else self.DIM
            r = radius
            w, h = width, height
            canvas.create_arc(0, 0, 2 * r, 2 * r, start=90, extent=90, fill=bg_color, outline=bg_color)
            canvas.create_arc(w - 2 * r, 0, w, 2 * r, start=0, extent=90, fill=bg_color, outline=bg_color)
            canvas.create_arc(0, h - 2 * r, 2 * r, h, start=180, extent=90, fill=bg_color, outline=bg_color)
            canvas.create_arc(w - 2 * r, h - 2 * r, w, h, start=270, extent=90, fill=bg_color, outline=bg_color)
            canvas.create_rectangle(r, 0, w - r, h, fill=bg_color, outline=bg_color)
            canvas.create_rectangle(0, r, w, h - r, fill=bg_color, outline=bg_color)
            canvas.create_text(w // 2, h // 2, text=state["text"], fill=fg_color, font=("Microsoft YaHei UI", font_size))

        def on_click(_e):
            if state["enabled"]: cmd()
        def set_text(t):
            if state["text"] == t: return
            state["text"] = t
            redraw()
        def set_state(enabled):
            state["enabled"] = enabled
            redraw()
        def on_enter(_e):
            state["hover"] = True
            redraw()
        def on_leave(_e):
            state["hover"] = False
            redraw()

        canvas.set_text = set_text
        canvas.set_state = set_state
        def _update_theme(panel):
            nonlocal canvas_bg, button_bg
            canvas_bg = panel.BG
            button_bg = panel.CARD
            canvas.configure(bg=canvas_bg)
            redraw()
        canvas._update_theme = _update_theme

        canvas.bind("<Button-1>", on_click)
        canvas.bind("<Enter>", on_enter)
        canvas.bind("<Leave>", on_leave)
        canvas.config(cursor="hand2")
        redraw()
        return canvas

    def _make_toggle_switch(self, parent, cmd, initial=True, width=48, height=24):
        canvas = tk.Canvas(parent, width=width, height=height, bg=self.CARD, highlightthickness=0, bd=0)
        pad = 3
        knob_d = height - pad * 2
        radius = height // 2
        state = {"value": bool(initial), "hover": False}

        def redraw():
            canvas.delete("all")
            v = state["value"]
            
            photo = self._get_toggle_photo(width, height, v, state["hover"])
            if photo is not None:
                canvas.create_image(width // 2, height // 2, image=photo)
                return

            if v:
                track = self.HOVER_ACCENT if state["hover"] else self.ACCENT
                knob = "#FFFFFF"
            else:
                track = self.HOVER_BG if state["hover"] else ("#D0D0D8" if not self.cfg.DARK_MODE else "#3D3D5C")
                knob = self.ACCENT
                
            canvas.create_oval(0, 0, height, height, fill=track, outline="")
            canvas.create_oval(width - height, 0, width, height, fill=track, outline="")
            canvas.create_rectangle(radius, 0, width - radius, height, fill=track, outline="")
            cy = height // 2
            cx = width - pad - knob_d // 2 if v else pad + knob_d // 2
            canvas.create_oval(cx - knob_d // 2, cy - knob_d // 2, cx + knob_d // 2, cy + knob_d // 2, fill=knob, outline="")

        def on_click(_e):
            state["value"] = not state["value"]
            redraw()
            cmd()
        def on_enter(_e):
            state["hover"] = True
            redraw()
        def on_leave(_e):
            state["hover"] = False
            redraw()
        def set_value(v):
            v = bool(v)
            if state["value"] == v: return
            state["value"] = v
            redraw()

        canvas.get_value = lambda: state["value"]
        canvas.set_value = set_value
        canvas._update_theme = lambda panel: (canvas.configure(bg=panel.CARD), redraw())

        canvas.bind("<Button-1>", on_click)
        canvas.bind("<Enter>", on_enter)
        canvas.bind("<Leave>", on_leave)
        canvas.config(cursor="hand2")
        redraw()
        return canvas

    def _make_play_btn(self, parent, text, cmd, diameter=50, font_size=16, primary=False):
        canvas = tk.Canvas(parent, width=diameter, height=diameter, bg=self.BG, highlightthickness=0, bd=0)
        state = {"text": text, "hover": False}

        def redraw():
            canvas.delete("all")
            d, c = diameter, diameter / 2
            if primary:
                fill = "#7A7AFF" if state["hover"] else self.ACCENT
                fg = "#FFFFFF"
            else:
                fill = self.SEL_BG if state["hover"] else self.CARD
                fg = self.FG

            photo = self._get_circle_photo(d, fill)
            if photo is not None:
                canvas.create_image(c, c, image=photo)
                canvas._circle_photo = photo
            else:
                canvas.create_oval(1, 1, d - 1, d - 1, fill=fill, outline="")

            txt = state["text"]

            if txt == "▶":
                tri_w, tri_h = font_size * 0.85, font_size * 1.05
                ox = tri_w * 0.1
                canvas.create_polygon(c - tri_w / 2 + ox, c - tri_h / 2, c - tri_w / 2 + ox, c + tri_h / 2, c + tri_w / 2 + ox, c, fill=fg, outline="")
            elif txt == "⏸":
                bar_w, bar_h = max(2, font_size * 0.22), font_size * 0.85
                gap = max(2, font_size * 0.18)
                total = bar_w * 2 + gap
                sx = c - total / 2
                canvas.create_rectangle(sx, c - bar_h / 2, sx + bar_w, c + bar_h / 2, fill=fg, outline="")
                canvas.create_rectangle(sx + bar_w + gap, c - bar_h / 2, sx + bar_w * 2 + gap, c + bar_h / 2, fill=fg, outline="")
            elif txt in ("◀◀", "▶▶"):
                tri_w, tri_h = font_size * 0.9, font_size * 1.1
                gap = font_size * 0.25
                total = tri_w * 2 + gap
                sx = c - total / 2
                for i in range(2):
                    tx = sx + i * (tri_w + gap)
                    if txt == "◀◀":
                        canvas.create_polygon(tx, c, tx + tri_w, c - tri_h / 2, tx + tri_w, c + tri_h / 2, fill=fg, outline="")
                    else:
                        canvas.create_polygon(tx, c - tri_h / 2, tx, c + tri_h / 2, tx + tri_w, c, fill=fg, outline="")
            else:
                canvas.create_text(c, c, text=txt, fill=fg, font=("Microsoft YaHei UI", font_size))

        def on_click(_e): cmd()
        def on_enter(_e):
            state["hover"] = True
            redraw()
        def on_leave(_e):
            state["hover"] = False
            redraw()
        def set_text(t):
            if state["text"] == t: return
            state["text"] = t
            redraw()

        canvas.set_text = set_text
        canvas._update_theme = lambda panel: (canvas.configure(bg=panel.BG), redraw())

        canvas.bind("<Button-1>", on_click)
        canvas.bind("<Enter>", on_enter)
        canvas.bind("<Leave>", on_leave)
        canvas.config(cursor="hand2")
        redraw()
        return canvas

    def _make_color_swatch(self, parent, color_key, label_text):
        frame = tk.Frame(parent, bg=self.CARD)
        lbl = tk.Label(frame, text=label_text, bg=self.CARD, fg=self.DIM, font=("Microsoft YaHei UI", 9))
        lbl.pack(side="left", padx=(0, 6))

        canvas = tk.Canvas(frame, width=22, height=22, bg=self.CARD, highlightthickness=0, bd=0)
        canvas.pack(side="left")
        state = {"hover": False}

        def redraw():
            canvas.delete("all")
            color = getattr(self.cfg, color_key)
            border = "#FFFFFF" if state["hover"] else ("#C0C0C8" if not self.cfg.DARK_MODE else "#5A5A78")
            x1, y1, x2, y2 = 1, 1, 21, 21
            r = 6
            points = [
                x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
                x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
                x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
            ]
            canvas.create_polygon(points, smooth=True, fill=color, outline=border, width=2)

        def on_click(_e): self._pick_color(color_key)
        def on_enter(_e):
            state["hover"] = True
            redraw()
        def on_leave(_e):
            state["hover"] = False
            redraw()

        def _update_theme(panel):
            frame.configure(bg=panel.CARD)
            lbl.configure(bg=panel.CARD, fg=panel.DIM)
            canvas.configure(bg=panel.CARD)
            redraw()
        canvas._update_theme = _update_theme

        canvas.bind("<Button-1>", on_click)
        canvas.bind("<Enter>", on_enter)
        canvas.bind("<Leave>", on_leave)
        canvas.config(cursor="hand2")
        redraw()
        return frame, redraw

    # ================= 新歌曲 =================
    def _ns_ensure_loaded(self):
        if self._ns_loaded and self._ns_albums:
            return
        self._ns_refresh()

    def _ns_refresh(self):
        if self.ns_list_wrap.winfo_ismapped():
            self._ns_back_to_grid()
        self.lbl_ns_title.config(text="加载最新专辑…")

        def work():
            try:
                albums = self.lms.browse_new_albums(60)
            except Exception as e:
                print(f"[新歌曲] 加载失败: {e}")
                albums = []
            try:
                self.panel.after(
                    0, lambda a=albums: self._ns_on_albums_loaded(a))
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _ns_on_albums_loaded(self, albums):
        self._ns_loaded = bool(albums)
        self._ns_view = "grid"
        if not albums:
            self.lbl_ns_title.config(text="未获取到最新专辑，点 ↻ 重试")
            return
        self.lbl_ns_title.config(text=f"最新专辑 · 共 {len(albums)} 张")
        self._ns_fill_albums(albums)

    def _ns_fill_albums(self, albums):
        self._ns_grid_panel_h = None        
        try:
            for w in self.ns_inner.winfo_children():
                w.destroy()
        except tk.TclError:
            return
        self._ns_albums = albums
        for i in range(3):
            self.ns_inner.grid_columnconfigure(i, weight=1, uniform="nscol")
        if not albums:
            return

        size = self._ns_cover_size
        for idx, (aid, name, _artist, coverid) in enumerate(albums):
            r, c = divmod(idx, 3)
            card = tk.Frame(self.ns_inner, bg=self.BG)
            card.grid(row=r, column=c, padx=4, pady=4, sticky="n")

            cover = tk.Canvas(card, width=size, height=size, bg=self.CARD,
                              highlightthickness=0, bd=0, cursor="hand2")
            cover.pack()
            self._ns_placeholder(cover)
            cover.bind("<Button-1>",
                       lambda e, a=aid, n=name: self._ns_open_album(a, n))

            name_lbl = tk.Label(card, text=name or "未知专辑", bg=self.BG,
                                fg=self.FG,
                                font=("Microsoft YaHei UI", 9),
                                wraplength=size, justify="center", anchor="n")
            name_lbl.pack(fill="x", pady=(4, 0))
            name_lbl.bind("<Button-1>",
                          lambda e, a=aid, n=name: self._ns_open_album(a, n))

            for w in (card, cover, name_lbl):
                w.bind("<MouseWheel>", self._ns_on_wheel)

            self._ns_enqueue_cover(aid, cover)

        try:
            self.ns_canvas.yview_moveto(0)
            self.ns_inner.update_idletasks()
            self.ns_canvas.configure(scrollregion=self.ns_canvas.bbox("all"))
        except tk.TclError:
            pass
        self.panel.after(60, self._ns_fit_grid_height)        

    def _ns_placeholder(self, canvas):
        try:
            s = self._ns_cover_size
            canvas.delete("all")
            canvas.create_rectangle(0, 0, s, s, fill=self.CARD, outline="")
            canvas.create_oval(s * 0.2, s * 0.2, s * 0.8, s * 0.8,
                               fill=self.SEL_BG, outline="")
            canvas.create_oval(s * 0.42, s * 0.42, s * 0.58, s * 0.58,
                               fill=self.ACCENT, outline="")
        except tk.TclError:
            pass

    # ---------- 高度自适应 ----------
    def _set_panel_height(self, h):
        try:
            self.panel.geometry(f"500x{int(h)}")
        except tk.TclError:
            pass

    def _ns_apply_layout_height(self):
        try:
            if self.nb.index(self.nb.select()) != 2:
                return
        except (tk.TclError, ValueError):
            return
        try:
            self.panel.update_idletasks()
        except tk.TclError:
            return
        if self._ns_view == "list":
            self._set_panel_height(480)
            return
        if self._ns_grid_panel_h:
            self._set_panel_height(self._ns_grid_panel_h)
            return
        self._ns_fit_grid_height()

    def _ns_fit_grid_height(self, retries=3):
        """让新歌曲网格视口恰好容纳 3 列 × 3 行。
           直接读当前 panel 与 canvas 高度求差作为固定部分高度，
           ★ 不改变 panel 大小：避免 WM resize 延迟导致 panel/canvas
             取到不同布局快照而使 other_h 偏大。
           布局尚未稳定时自动重试。"""
        if self._ns_view != "grid":
            return
        try:
            if self.nb.index(self.nb.select()) != 2:
                return
        except (tk.TclError, ValueError):
            return

        try:
            self.panel.update_idletasks()
        except tk.TclError:
            return

        try:
            # ① 目标内容高度 content_h（3 行）
            children = self.ns_inner.winfo_children()
            if not children:
                try:
                    f = tkfont.Font(family="Microsoft YaHei UI", size=9)
                    name_h = f.metrics("linespace")
                except tk.TclError:
                    name_h = 20
                content_h = 3 * (self._ns_cover_size + 4 + name_h) + 24
            else:
                def _row_h(i):
                    cs = children[i * 3: i * 3 + 3]
                    return max(c.winfo_reqheight() for c in cs) if cs else 0
                row_hs = [_row_h(i) for i in range(3)]
                row_hs = [h for h in row_hs if h > 0]
                content_h = sum(row_hs) + 8 * len(row_hs)

            # ② 读当前同一次布局快照下的 panel / canvas 高度
            panel_h = self.panel.winfo_height()
            canvas_h = self.ns_canvas.winfo_height()

            # ③ 合理性检查（布局未稳定 → 重试）
            if panel_h < 100 or canvas_h < 20 or canvas_h >= panel_h:
                if retries > 0:
                    self.panel.after(
                        80, lambda: self._ns_fit_grid_height(retries - 1))
                return

            other_h = panel_h - canvas_h
            new_h = max(280, min(int(other_h + content_h), 1000))
            self._ns_grid_panel_h = new_h
            if abs(new_h - panel_h) > 2:
                self._set_panel_height(new_h)
        except (tk.TclError, ValueError):
            pass

    def _ns_on_wheel(self, event):
        if not self.ns_grid_wrap.winfo_ismapped():
            return None
        try:
            delta = event.delta
        except AttributeError:
            return None
        if not delta:
            return None
        steps = int(-delta / 120)
        if steps == 0:
            steps = -1 if delta > 0 else 1
        try:
            self.ns_canvas.yview_scroll(steps * 2, "units")
        except tk.TclError:
            pass
        return "break"

    def _ns_open_album(self, album_id, album_name):
        if not album_id:
            return
        album_info = None
        for a in self._ns_albums:
            if str(a[0]) == str(album_id):
                album_info = a  # (aid, name, artist, coverid)
                break
        artist_name = album_info[2] if album_info else ""
        coverid = album_info[3] if album_info else ""

        self.lbl_ns_title.config(text=f"加载 {album_name} …")
        try:
            self.btn_ns_back.pack(side="left", padx=(0, 4),
                                  before=self.lbl_ns_title)
        except tk.TclError:
            pass

        def work():
            try:
                tracks = self.lms.browse_tracks(album_id)
            except Exception as e:
                print(f"[新歌曲] 加载专辑歌曲失败: {e}")
                tracks = []
            try:
                self.panel.after(
                    0, lambda t=tracks, n=album_name, ar=artist_name,
                    ai=album_id, cv=coverid:
                    self._ns_show_tracks(t, n, ar, ai, cv))
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()
    def _ns_set_now_text(self, text):
        """底部中间歌名：单行显示，宽度不足时自动缩小字号（最小 8pt）。"""
        if not text:
            try:
                self.lbl_ns_now.config(text="")
            except tk.TclError:
                pass
            return
        try:
            self.panel.update_idletasks()
            avail = self.ns_center.winfo_width()
        except tk.TclError:
            avail = 0
        if avail <= 10:
            avail = 200
        max_w = max(60, avail - 4)

        size = 11
        final = 8
        while size >= 8:
            try:
                f = tkfont.Font(family="Microsoft YaHei UI",
                                size=size, weight="bold")
            except tk.TclError:
                break
            if f.measure(text) <= max_w:
                final = size
                break
            size -= 1

        try:
            self.lbl_ns_now.config(
                text=text,
                font=("Microsoft YaHei UI", final, "bold"))
        except tk.TclError:
            pass
            
    def _ns_set_album_text(self, text):
        """底部右侧专辑名：单行显示，宽度不足时自动缩小字号（最小 9pt），
           不使用省略号。"""
        if not text:
            try:
                self.lbl_ns_album.config(
                    text="", font=("Microsoft YaHei UI", 18, "bold"))
            except tk.TclError:
                pass
            return
        try:
            self.panel.update_idletasks()
            bar_w = self.ns_bottom_bar.winfo_width()
            center_visible = bool(self.ns_center.winfo_ismapped())
        except tk.TclError:
            bar_w = 0
            center_visible = False
        if bar_w <= 100:
            bar_w = 480

        reserve = 140 if center_visible else 8
        max_w = max(80, bar_w - 72 - 22 - reserve - 24)

        size = 18
        final = 9
        while size >= 9:
            try:
                f = tkfont.Font(family="Microsoft YaHei UI",
                                size=size, weight="bold")
            except tk.TclError:
                break
            if f.measure(text) <= max_w:
                final = size
                break
            size -= 1

        try:
            self.lbl_ns_album.config(
                text=text,
                font=("Microsoft YaHei UI", final, "bold"))
        except tk.TclError:
            pass
        
    def _ns_show_tracks(self, tracks, album_name,
                        artist_name="", album_id="", coverid=""):
        try:
            self.ns_grid_wrap.pack_forget()
            self.ns_list_wrap.pack(fill="both", expand=True)
            self._ns_view = "list"

            self.ns_tree.delete(*self.ns_tree.get_children())
            self._ns_tracks.clear()
            for tid, tno, tname in tracks:
                iid = f"nstr{tid}"
                label = f"{tno}. {tname}" if tno else tname
                self.ns_tree.insert("", "end", iid=iid, text=label)
                self._ns_tracks[iid] = (str(tid), tname)

            self.lbl_ns_title.config(text=f"{album_name} · {len(tracks)} 首")
            self.lbl_ns_artist.config(text=artist_name or "")

            self._ns_current_album = (
                album_id, album_name, artist_name, coverid)

            self._ns_load_album_cover(album_id)
            # 先更新中间区显隐，再让 _update_ns_now_playing 内部统一适配专辑名字号
            self._update_ns_now_playing(force=True)

            # 打开专辑时：如果正在播放的正是本专辑某首，直接选中它；
            # 否则默认选中第一首。
            self._ns_last_synced_track = None
            ti = self.track_info or {}
            cur_album = (ti.get("album", "") or "").strip()
            cur_tid = str(ti.get("track_id") or "")
            target_iid = None
            if (cur_tid and album_name
                    and cur_album == album_name.strip()):
                cand = f"nstr{cur_tid}"
                if cand in self._ns_tracks:
                    target_iid = cand
                    self._ns_last_synced_track = cur_tid

            children = self.ns_tree.get_children()
            if target_iid:
                self.ns_tree.selection_set(target_iid)
                self.ns_tree.focus(target_iid)
                self.ns_tree.see(target_iid)
                self.ns_tree.focus_set()
            elif children:
                self.ns_tree.selection_set(children[0])
                self.ns_tree.focus(children[0])
                self.ns_tree.focus_set()                         
        except tk.TclError:
            pass
        self.panel.after(10, self._ns_apply_layout_height)         
        try:
            self.panel.after(30, lambda: self._redraw_hearts(
                self.ns_tree, self.ns_tree_hearts, self._ns_tracks))
        except tk.TclError:
            pass
        
    def _ns_back_to_grid(self):
        try:
            self.ns_list_wrap.pack_forget()
            self.ns_grid_wrap.pack(fill="both", expand=True)
            self.btn_ns_back.pack_forget()
            self._ns_view = "grid"
            self._ns_current_album = None
            self._ns_last_synced_track = None   # ★ 新增
            self.lbl_ns_title.config(
                text=f"最新专辑 · 共 {len(self._ns_albums)} 张")
            self.panel.focus_set()
        except tk.TclError:
            pass
        self.panel.after(10, self._ns_apply_layout_height)        

    def _ns_on_escape(self, _e=None):
        self._ns_back_to_grid()
        return "break"

    def _on_ns_track_activate(self, _e=None):
        sel = self.ns_tree.selection()
        if not sel:
            return "break"
        info = self._ns_tracks.get(sel[0])
        if not info:
            return "break"
        tid, tname = info
        self.lbl_ns_now.config(text=tname)
        self._ns_last_synced_track = tid
        self._play_mode = "smart"      # ★ 恢复智能切歌

        def work():
            try:
                self.lms.play_track(tid)
            except Exception:
                traceback.print_exc()

        threading.Thread(target=work, daemon=True).start()
        return "break"

    # ---------- 底部大图 ----------
    def _ns_load_album_cover(self, album_id):
        """底部大图：同样用首曲封面。"""
        key = album_id
        if key in self._ns_album_cover_cache:
            self._ns_set_album_cover(self._ns_album_cover_cache[key])
            return

        try:
            c = self.ns_album_cover
            c.delete("all")
            c.create_rectangle(0, 0, 72, 72, fill=self.SEL_BG, outline="")
            c.create_oval(20, 20, 52, 52, fill=self.ACCENT, outline="")
        except tk.TclError:
            pass
        if not HAS_PIL:
            return

        def work():
            data = self._ns_resolve_cover(album_id)
            if not data:
                return
            try:
                img = Image.open(BytesIO(data)).convert("RGB")
                w, h = img.size
                side = min(w, h)
                left = (w - side) // 2
                top = (h - side) // 2
                img = img.crop((left, top, left + side, top + side))
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS
                img = img.resize((72, 72), resample)
            except Exception:
                return
            try:
                self.panel.after(0, lambda im=img, k=key:
                                 self._ns_set_album_cover_pil(im, k))
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _ns_set_album_cover_pil(self, img, key):
        try:
            photo = ImageTk.PhotoImage(img)
            self._ns_album_cover_cache[key] = photo
            self._ns_set_album_cover(photo)
        except Exception:
            pass

    def _ns_set_album_cover(self, photo):
        try:
            c = self.ns_album_cover
            c.delete("all")
            c.create_image(36, 36, image=photo)
            c.image = photo
        except tk.TclError:
            pass

    def _ns_set_album_cover_pil(self, img, key):
        try:
            photo = ImageTk.PhotoImage(img)
            self._ns_album_cover_cache[key] = photo
            self._ns_set_album_cover(photo)
        except Exception:
            pass

    def _ns_set_album_cover(self, photo):
        try:
            c = self.ns_album_cover
            c.delete("all")
            c.create_image(36, 36, image=photo)
            c.image = photo
        except tk.TclError:
            pass

    # ---------- 正在播放行 ----------
    def _update_ns_now_playing(self, force=False):
        if not self._ns_current_album:
            return
        if not force and not self.ns_list_wrap.winfo_ismapped():
            return
        _album_id, album_name, _artist, _coverid = self._ns_current_album
        ti = self.track_info or {}
        cur_album = (ti.get("album", "") or "").strip()
        cur_title = (ti.get("title", "") or "").strip()
        show = (bool(cur_title)
                and bool(album_name)
                and cur_album == album_name.strip())
        try:
            if show:
                if not self.ns_center.winfo_ismapped():
                    # after=封面：保持右侧 → 封面 → 中间的 pack 顺序
                    self.ns_center.pack(side="left", fill="both",
                                        expand=True, pady=12,
                                        after=self.ns_album_cover)
                    try:
                        self.panel.update_idletasks()
                    except tk.TclError:
                        pass
                self._ns_set_now_text(cur_title)

                # 布局稳定后按真实宽度再修正一次字号
                def _defer(t=cur_title, an=album_name):
                    try:
                        if not self._ns_current_album:
                            return
                        if self._ns_current_album[1] != an:
                            return
                        self._ns_set_now_text(t)
                    except tk.TclError:
                        pass
                try:
                    self.panel.after(30, _defer)
                except tk.TclError:
                    pass
            else:
                if self.ns_center.winfo_ismapped():
                    self.ns_center.pack_forget()
                    try:
                        self.panel.update_idletasks()
                    except tk.TclError:
                        pass

            # 中间区显隐会改变右侧可用宽度 → 重新适配专辑名字号
            self._ns_set_album_text(album_name)
        except tk.TclError:
            pass

    def _ns_sync_selection(self):
        """播放切到新曲时，让新歌曲列表的选中项跟进到正在播放的歌曲。
           仅在 track_id 变化时写一次 selection，避免打断用户手动选择。"""
        if not self._ns_current_album or self._ns_view != "list":
            return
        try:
            if not self.ns_list_wrap.winfo_ismapped():
                return
        except tk.TclError:
            return

        ti = self.track_info or {}
        cur_album = (ti.get("album", "") or "").strip()
        cur_tid = str(ti.get("track_id") or "")
        if not cur_tid:
            return

        _album_id, album_name, _artist, _coverid = self._ns_current_album
        if not album_name or cur_album != album_name.strip():
            return

        iid = f"nstr{cur_tid}"
        if iid not in self._ns_tracks:
            return

        # 同一个 track_id 不重复写 selection，避免每 100ms 覆盖用户手动选择
        if self._ns_last_synced_track == cur_tid:
            return
        self._ns_last_synced_track = cur_tid
        
        try:
            self._redraw_hearts(
                self.ns_tree, self.ns_tree_hearts, self._ns_tracks)
        except Exception:
            pass
        
        try:
            cur_sel = self.ns_tree.selection()
            if cur_sel and cur_sel[0] == iid:
                return
            self.ns_tree.selection_set(iid)
            self.ns_tree.focus(iid)
            self.ns_tree.see(iid)
        except tk.TclError:
            pass
        try:
            self._redraw_hearts(
                self.ns_tree, self.ns_tree_hearts, self._ns_tracks)
        except Exception:
            pass
        
    # ---------- 封面异步加载（网格） ----------
    def _ns_enqueue_cover(self, album_id, canvas):
        key = album_id
        if key in self._ns_cover_cache:
            self._ns_apply_cover(canvas, self._ns_cover_cache[key])
            return
        if not HAS_PIL:
            return
        self._ns_cover_queue.put((album_id, canvas, key))

    def _ns_cover_worker(self):
        while True:
            try:
                album_id, canvas, key = self._ns_cover_queue.get()
            except Exception:
                return

            if key in self._ns_cover_cache:
                try:
                    self.panel.after(
                        0, lambda cv=canvas, p=self._ns_cover_cache[key]:
                        self._ns_apply_cover(cv, p))
                except tk.TclError:
                    pass
                continue

            data = self._ns_resolve_cover(album_id)
            if not data:
                continue

            try:
                img = Image.open(BytesIO(data)).convert("RGB")
                w, h = img.size
                side = min(w, h)
                left = (w - side) // 2
                top = (h - side) // 2
                img = img.crop((left, top, left + side, top + side))
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS
                img = img.resize((self._ns_cover_size, self._ns_cover_size),
                                 resample)
            except Exception:
                continue

            try:
                self.panel.after(
                    0, lambda cv=canvas, im=img, k=key:
                    self._ns_apply_cover_pil(cv, im, k))
            except tk.TclError:
                pass

    def _ns_apply_cover_pil(self, canvas, img, key):
        try:
            if not canvas.winfo_exists():
                return
            photo = ImageTk.PhotoImage(img)
            self._ns_cover_cache[key] = photo
            self._ns_apply_cover(canvas, photo)
        except Exception:
            pass

    def _ns_apply_cover(self, canvas, photo):
        try:
            if not canvas.winfo_exists():
                return
            s = self._ns_cover_size
            canvas.delete("all")
            canvas.create_image(s // 2, s // 2, image=photo)
            canvas.image = photo
        except tk.TclError:
            pass

    # ---------- 首曲封面解析（核心） ----------
    def _ns_get_first_track_ids(self, album_id):
        """查专辑首曲的 (coverid, track_id)，带缓存。"""
        if album_id in self._ns_first_track_cache:
            return self._ns_first_track_cache[album_id]
        result = ("", "")
        try:
            res = self.lms.command(
                ["titles", 0, 1, f"album_id:{album_id}", "tags:K"],
                player="-")
            loop = res.get("titles_loop", []) or []
            if loop:
                t = loop[0]
                result = (str(t.get("coverid") or ""),
                          str(t.get("id") or ""))
        except Exception as e:
            print(f"[新歌曲] 查询首曲失败 album={album_id}: {e}")
        self._ns_first_track_cache[album_id] = result
        return result

    def _ns_resolve_cover(self, album_id):
        """只用「首曲 coverid / track_id」请求封面。
           （不用 album_id 兜底：LMS 会返回默认占位图，导致误判成功）"""
        first_cover, first_tid = self._ns_get_first_track_ids(album_id)
        candidates = []
        if first_cover and first_cover not in ("0", "-"):
            candidates.append(first_cover)
        if first_tid and first_tid not in candidates:
            candidates.append(first_tid)
        if not candidates:
            return None
        return self._ns_try_cover_urls(self.lms.host, self.lms.port, candidates)

    @staticmethod
    def _ns_try_cover_urls(host, port, ids):
        """对每个 id 依次尝试 cover.jpg / cover_200x200.jpg；命中返回字节。"""
        for id_ in ids:
            for suffix in ("cover.jpg", "cover_200x200.jpg"):
                url = f"http://{host}:{port}/music/{id_}/{suffix}"
                try:
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "lms-karaoke/1.0"})
                    with urllib.request.urlopen(req, timeout=8) as r:
                        data = r.read()
                    if not data or len(data) < 100:
                        continue
                    if (data[:3] == b"\xff\xd8\xff"
                            or data[:8] == b"\x89PNG\r\n\x1a\n"):
                        return data
                except Exception:
                    continue
        return None
    
    # ================= 圆形返回按钮 =================
    def _get_back_icon(self, size, color, hover):
        """PIL 超采样生成平滑的 chevron ‹ 图标，按 (size, color, hover) 缓存。"""
        if not hasattr(self, "_back_icon_cache"):
            self._back_icon_cache = {}
        key = (size, color, hover)
        if key in self._back_icon_cache:
            return self._back_icon_cache[key]
        if not HAS_PIL:
            return None
        try:
            ss = 4
            W = size * ss
            img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            if hover:
                d.ellipse((0, 0, W - 1, W - 1), fill=self.SEL_BG)
            lw = max(2, int(W * 0.09))
            r = lw // 2
            cy = W * 0.5
            x_tip = W * 0.38
            x_end = W * 0.62
            y_top = W * 0.30
            y_bot = W * 0.70
            for seg in ([(x_end, y_top), (x_tip, cy)],
                        [(x_tip, cy), (x_end, y_bot)]):
                d.line(seg, fill=color, width=lw)
            for px, py in ((x_end, y_top), (x_tip, cy), (x_end, y_bot)):
                d.ellipse((px - r, py - r, px + r, py + r), fill=color)
            small = img.resize((size, size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(small)
            self._back_icon_cache[key] = photo
            return photo
        except Exception:
            return None

    def _make_server_button(self, parent, cmd, size=22):
        """无文字的服务器图标按钮：非 hover 仅显示图标；
           hover 出现 SEL_BG 圆底，风格与 _make_back_button 一致。"""
        canvas = tk.Canvas(parent, width=size, height=size, bg=self.BG,
                           highlightthickness=0, bd=0, cursor="hand2")
        state = {"hover": False}

        def redraw():
            try:
                canvas.delete("all")
                color = self.FG
                photo = self._get_server_icon(size, color, state["hover"])
                if photo is not None:
                    canvas.create_image(size // 2, size // 2, image=photo)
                    canvas._img = photo
                else:
                    # PIL 不可用：emoji 兜底
                    if state["hover"]:
                        canvas.create_oval(1, 1, size - 1, size - 1,
                                           fill=self.ACCENT, outline="")
                        text_color = "#FFFFFF"
                    else:
                        text_color = self.ACCENT
                    canvas.create_text(size // 2, size // 2, text="🖥",
                                       fill=text_color,
                                       font=("Segoe UI Emoji",
                                             max(9, size - 10)))
            except tk.TclError:
                pass

        def on_click(_e):
            cmd()

        def on_enter(_e):
            state["hover"] = True
            redraw()

        def on_leave(_e):
            state["hover"] = False
            redraw()

        def _update_theme(panel):
            canvas.configure(bg=panel.BG)
            redraw()

        canvas._update_theme = _update_theme

        canvas.bind("<Button-1>", on_click)
        canvas.bind("<Enter>", on_enter)
        canvas.bind("<Leave>", on_leave)
        redraw()
        return canvas
    
    def _make_back_button(self, parent, cmd, size=24):
        """无文字的圆形返回按钮：非 hover 只显示箭头；
           hover 出现 SEL_BG 圆底，整体风格与主面板按钮一致。"""
        canvas = tk.Canvas(parent, width=size, height=size, bg=self.BG,
                           highlightthickness=0, bd=0, cursor="hand2")
        state = {"hover": False}

        def redraw():
            try:
                canvas.delete("all")
                color = self.FG
                photo = self._get_back_icon(size, color, state["hover"])
                if photo is not None:
                    canvas.create_image(size // 2, size // 2, image=photo)
                    canvas._img = photo
                else:
                    # PIL 不可用：canvas 原生画线兜底
                    if state["hover"]:
                        canvas.create_oval(1, 1, size - 1, size - 1,
                                           fill=self.SEL_BG, outline="")
                    w = max(2, size // 12)
                    canvas.create_line(
                        size * 0.62, size * 0.30,
                        size * 0.40, size * 0.50,
                        size * 0.62, size * 0.70,
                        fill=color, width=w,
                        capstyle="round", joinstyle="round")
            except tk.TclError:
                pass

        def on_click(_e):
            cmd()

        def on_enter(_e):
            state["hover"] = True
            redraw()

        def on_leave(_e):
            state["hover"] = False
            redraw()

        def _update_theme(panel):
            canvas.configure(bg=panel.BG)
            redraw()

        canvas._update_theme = _update_theme

        canvas.bind("<Button-1>", on_click)
        canvas.bind("<Enter>", on_enter)
        canvas.bind("<Leave>", on_leave)
        redraw()
        return canvas

    # ================= 简洁模式 =================
    def _toggle_compact_mode(self):
        if self._compact_mode:
            self._exit_compact_mode()
        else:
            self._enter_compact_mode()

    def _enter_compact_mode(self):
        if self._compact_mode:
            return
        self._compact_mode = True
        self._compact_saved = {}

        # 保存几何
        try:
            self._compact_saved["geometry"] = self.panel.geometry()
        except Exception:
            self._compact_saved["geometry"] = "500x365+100+100"

        # 隐藏 head 与整个 Notebook（彻底去除 tab 栏）
        try:
            self.head.pack_forget()
        except Exception:
            pass
        try:
            self.nb.pack_forget()
        except Exception:
            pass
        
        # 独立简洁视图（去除四周留白）
        self._compact_root = tk.Frame(self.panel, bg=self.BG)
        self._compact_root.pack(fill="both", expand=True,
                                padx=2, pady=2)

        card = tk.Frame(self._compact_root, bg=self.CARD)
        card.pack(fill="both", expand=True)
        self._compact_card = card

        # 左侧封面
        cover_frame = tk.Frame(card, bg=self.CARD)
        cover_frame.pack(side="left", fill="y", padx=(6, 4))
        self._compact_cover = tk.Canvas(cover_frame, width=72, height=72,
                                        bg=self.CARD,
                                        highlightthickness=0, bd=0)
        self._compact_cover.pack(expand=True)
        self._copy_cover_to_compact()

        # 右侧信息区
        info = tk.Frame(card, bg=self.CARD)
        info.pack(side="left", fill="both", expand=True,
                  padx=(0, 6), pady=4)

        # ① 底部行：控制按钮，整体右对齐（右下角）
        bottom_row = tk.Frame(info, bg=self.CARD)
        bottom_row.pack(side="bottom", fill="x", pady=(2, 0))
        self._compact_bottom_row = bottom_row
        self._compact_ctl = bottom_row

        # ★ 用 side="right" 逆序打包，视觉顺序：◀◀ ▶ ▶▶ 贴右下角
        self._compact_next = self._make_small_play_btn(
            bottom_row, "▶▶", lambda: self._cmd("next"), is_play=False)
        self._compact_next.pack(side="right", padx=2)

        play_icon = "⏸" if self._playing else "▶"
        self._compact_play = self._make_small_play_btn(
            bottom_row, play_icon, self._toggle_play, is_play=True)
        self._compact_play.pack(side="right", padx=2)

        self._compact_prev = self._make_small_play_btn(
            bottom_row, "◀◀", lambda: self._cmd("prev"), is_play=False)
        self._compact_prev.pack(side="right", padx=2)

        # ② 标题 / 歌手（完整定义，不可用占位符）
        self._compact_title = tk.Label(
            info, text=self.lbl_title.cget("text"),
            bg=self.CARD, fg=self.FG,
            font=("Microsoft YaHei UI", 14, "bold"),
            anchor="w", justify="left", wraplength=260)
        self._compact_title.pack(side="top", fill="x", anchor="w")

        self._compact_artist = tk.Label(
            info, text=self.lbl_artist.cget("text"),
            bg=self.CARD, fg=self.DIM,
            font=("Microsoft YaHei UI", 10),
            anchor="w", justify="left", wraplength=260)
        self._compact_artist.pack(side="top", fill="x", anchor="w",
                                  pady=(2, 0))

        # ③ 恢复按钮：卡片右上角
        self._compact_restore = self._make_restore_button(card)
        self._compact_restore.place(relx=1.0, x=-3, y=3, anchor="ne")

        # ⑤ 无边框 + 置顶
        try:
            self.panel.overrideredirect(True)
            self.panel.attributes("-topmost", True)
        except tk.TclError:
            pass

        # ⑥ 窗口尺寸：留白缩小后重新计算，卡片更紧凑
        try:
            self.panel.geometry("400x100")
        except tk.TclError:
            pass
        self.panel.update_idletasks()
        try:
            self.panel.update_idletasks()
            req = self._compact_root.winfo_reqheight()
            if req < 60:
                req = 86  # 兜底
            h = max(76, min(req + 4, 200))
            self.panel.geometry(f"400x{h}")
        except tk.TclError:
            pass

        self._bind_compact_drag()
      

    def _copy_cover_to_compact(self):
        try:
            c = self._compact_cover
            if c is None or not c.winfo_exists():
                return
            c.delete("all")
            if self._cover_photo is not None:
                c.create_image(36, 36, image=self._cover_photo)
                c.image = self._cover_photo
            else:
                dark = self.cfg.DARK_MODE
                c.create_oval(4, 4, 68, 68,
                              fill="#2A2A3A" if dark else "#D0D0D8",
                              outline="#3D3D5C", width=2)
                c.create_oval(22, 22, 50, 50, fill=self.ACCENT, outline="")
                c.create_oval(33, 33, 39, 39, fill=self.CARD, outline="")
        except tk.TclError:
            pass

    def _make_small_play_btn(self, parent, text, cmd, is_play=False):
        size = 22 if is_play else 18
        canvas = tk.Canvas(parent, width=size, height=size, bg=self.CARD,
                           highlightthickness=0, bd=0, cursor="hand2")
        state = {"text": text, "hover": False}

        def redraw():
            try:
                canvas.delete("all")
                d, c = size, size / 2
                if is_play:
                    fill = "#7A7AFF" if state["hover"] else self.ACCENT
                    fg = "#FFFFFF"
                    photo = self._get_circle_photo(size, fill, bg=self.CARD)
                    if photo is not None:
                        canvas.create_image(c, c, image=photo)
                        canvas._circle_photo = photo
                    else:
                        canvas.create_oval(0, 0, d, d, fill=fill, outline="")
                else:
                    fill = self.SEL_BG
                    fg = self.FG
                    # 非播放按钮：仅 hover 时显示圆底
                    if state["hover"]:
                        photo = self._get_circle_photo(size, fill, bg=self.CARD)
                        if photo is not None:
                            canvas.create_image(c, c, image=photo)
                            canvas._circle_photo = photo
                        else:
                            canvas.create_oval(1, 1, d - 1, d - 1,
                                               fill=fill, outline="")

                txt = state["text"]
                if txt == "▶":
                    tw, th = size * 0.30, size * 0.36
                    ox = tw * 0.12
                    canvas.create_polygon(
                        c - tw / 2 + ox, c - th / 2,
                        c - tw / 2 + ox, c + th / 2,
                        c + tw / 2 + ox, c,
                        fill=fg, outline="")
                elif txt == "⏸":
                    bw = max(2, size * 0.15)
                    bh = size * 0.40
                    gap = max(2, size * 0.10)
                    total = bw * 2 + gap
                    sx = c - total / 2
                    canvas.create_rectangle(sx, c - bh / 2,
                                            sx + bw, c + bh / 2,
                                            fill=fg, outline="")
                    canvas.create_rectangle(sx + bw + gap, c - bh / 2,
                                            sx + bw * 2 + gap, c + bh / 2,
                                            fill=fg, outline="")
                elif txt in ("◀◀", "▶▶"):
                    tw, th = size * 0.30, size * 0.38
                    gap = size * 0.06
                    total = tw * 2 + gap
                    sx = c - total / 2
                    for i in range(2):
                        tx = sx + i * (tw + gap)
                        if txt == "◀◀":
                            canvas.create_polygon(
                                tx, c,
                                tx + tw, c - th / 2,
                                tx + tw, c + th / 2,
                                fill=fg, outline="")
                        else:
                            canvas.create_polygon(
                                tx, c - th / 2,
                                tx, c + th / 2,
                                tx + tw, c,
                                fill=fg, outline="")
            except tk.TclError:
                pass

        def set_text(t):
            state["text"] = t
            redraw()

        canvas.set_text = set_text
        canvas._update_theme = lambda p: (canvas.configure(bg=p.CARD), redraw())

        canvas.bind("<Button-1>", lambda e: cmd())
        canvas.bind("<Enter>",
                    lambda e: (state.update({"hover": True}), redraw()))
        canvas.bind("<Leave>",
                    lambda e: (state.update({"hover": False}), redraw()))
        redraw()
        return canvas

    def _get_speaker_photo(self, size=14):
        """PIL 手绘音箱图标：竖长方体箱体 + 上方小圆高音 + 下方大圆低音。
           颜色固定用 ACCENT，与 head 里的服务器图标同色同风格。"""
        if not hasattr(self, "_speaker_photo_cache"):
            self._speaker_photo_cache = {}
        key = size
        if key in self._speaker_photo_cache:
            return self._speaker_photo_cache[key]
        if not HAS_PIL:
            return None
        try:
            ss = 4
            W = size * ss
            img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            color = self.ACCENT
            stroke = max(1, int(W * 0.09))

            pad_x = W * 0.20
            pad_y = W * 0.08
            try:
                d.rounded_rectangle(
                    (pad_x, pad_y, W - pad_x, W - pad_y),
                    radius=int(W * 0.10),
                    outline=color, width=stroke)
            except Exception:
                d.rectangle((pad_x, pad_y, W - pad_x, W - pad_y),
                            outline=color, width=stroke)

            cx = W / 2
            top_cy = W * 0.34
            top_r = W * 0.11
            d.ellipse((cx - top_r, top_cy - top_r,
                       cx + top_r, top_cy + top_r),
                      fill=color)

            bot_cy = W * 0.68
            bot_r = W * 0.17
            d.ellipse((cx - bot_r, bot_cy - bot_r,
                       cx + bot_r, bot_cy + bot_r),
                      outline=color, width=stroke)
            dot_r = bot_r * 0.4
            d.ellipse((cx - dot_r, bot_cy - dot_r,
                       cx + dot_r, bot_cy + dot_r),
                      fill=color)

            small = img.resize((size, size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(small)
            self._speaker_photo_cache[key] = photo
            return photo
        except Exception:
            return None

    def _get_server_icon(self, size, color, hover):
        """PIL 超采样手绘服务器机柜：三层机架 + 左侧指示灯 + 右侧硬盘槽。
           hover=True：ACCENT 圆底 + 白色机柜（明确的"可点击"视觉提示）
           hover=False：无底色，机柜用传入的 color（主题前景色）"""
        if not hasattr(self, "_server_icon_cache"):
            self._server_icon_cache = {}
        key = (size, color, hover)
        if key in self._server_icon_cache:
            return self._server_icon_cache[key]
        if not HAS_PIL:
            return None
        try:
            ss = 4
            W = size * ss
            img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)

            # ★ 悬浮时：ACCENT 圆底 + 白色图标
            # ★ 未悬浮：图标本身用 ACCENT（高亮色），与 IP 文字同色
            if hover:
                d.ellipse((0, 0, W - 1, W - 1), fill=self.ACCENT)
                icon_color = "#FFFFFF"
            else:
                icon_color = self.ACCENT

            pad = W * 0.16
            rack_w = W - pad * 2
            rack_h = (W - pad * 2) / 3.7
            gap = (W - pad * 2 - rack_h * 3) / 2
            stroke = max(1, int(W * 0.045))

            for i in range(3):
                y = pad + i * (rack_h + gap)
                # 机架外框
                d.rectangle((pad, y, pad + rack_w, y + rack_h),
                            outline=icon_color, width=stroke)

                # 左侧指示灯（实心圆）
                led_r = rack_h * 0.16
                led_cx = pad + rack_h * 0.42
                led_cy = y + rack_h / 2
                d.ellipse((led_cx - led_r, led_cy - led_r,
                           led_cx + led_r, led_cy + led_r),
                          fill=icon_color)

                # 右侧硬盘槽（两条短粗横条）
                for k in range(2):
                    sx = pad + rack_w * 0.42 + k * rack_w * 0.22
                    sy = y + rack_h * 0.34
                    d.rectangle((sx, sy,
                                 sx + rack_w * 0.16,
                                 sy + rack_h * 0.32),
                                fill=icon_color)

            small = img.resize((size, size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(small)
            self._server_icon_cache[key] = photo
            return photo
        except Exception:
            return None
            
    # ---------- 图钉图标 ----------
    def _get_pin_icon(self, size, color, hover):
        """PIL 超采样绘制图钉（扁圆头 + 锥形针），按 (size, color, hover) 缓存。"""
        if not hasattr(self, "_pin_icon_cache"):
            self._pin_icon_cache = {}
        key = (size, color, hover)
        if key in self._pin_icon_cache:
            return self._pin_icon_cache[key]
        if not HAS_PIL:
            return None
        try:
            ss = 4
            W = size * ss
            img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)

            # hover：圆底
            if hover:
                d.ellipse((0, 0, W - 1, W - 1), fill=self.SEL_BG)

            cx = W / 2

            # 扁圆头
            hw = W * 0.28
            hh = W * 0.20
            hcy = W * 0.34
            d.ellipse((cx - hw, hcy - hh, cx + hw, hcy + hh),
                      fill=color)

            # 锥形针
            n_top = hcy + hh * 0.5
            n_bot = W * 0.86
            lw = W * 0.09
            d.polygon(
                [(cx - lw, n_top), (cx + lw, n_top), (cx, n_bot)],
                fill=color)

            # 圆头高光（左上）
            hg = W * 0.05
            hlx = cx - hw * 0.45
            hly = hcy - hh * 0.45
            d.ellipse((hlx - hg, hly - hg, hlx + hg, hly + hg),
                      fill=(255, 255, 255, 220))

            small = img.resize((size, size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(small)
            self._pin_icon_cache[key] = photo
            return photo
        except Exception:
            return None
        
    def _make_restore_button(self, parent, size=15):
        """恢复完整功能按钮：实心圆（PIL 平滑）。"""
        canvas = tk.Canvas(parent, width=size, height=size, bg=self.CARD,
                           highlightthickness=0, bd=0, cursor="hand2")
        state = {"hover": False}

        def redraw():
            try:
                canvas.delete("all")
                fill = self.HOVER_ACCENT if state["hover"] else self.ACCENT
                photo = self._get_circle_photo(size, fill, bg=self.CARD)
                if photo is not None:
                    canvas.create_image(size // 2, size // 2, image=photo)
                    canvas._circle_photo = photo
                else:
                    c = size / 2
                    r = size / 2 - 2
                    canvas.create_oval(c - r, c - r, c + r, c + r,
                                       fill=fill, outline="")
            except tk.TclError:
                pass

        canvas._update_theme = lambda p: (canvas.configure(bg=p.CARD),
                                          redraw())

        canvas.bind("<Button-1>", lambda e: self._exit_compact_mode())
        canvas.bind("<Enter>",
                    lambda e: (state.update({"hover": True}), redraw()))
        canvas.bind("<Leave>",
                    lambda e: (state.update({"hover": False}), redraw()))
        redraw()
        return canvas

    def _exit_compact_mode(self):
        if not self._compact_mode:
            return
        self._compact_mode = False

        # 解绑拖拽全局捕获，避免影响完整模式
        try:
            self.panel.unbind_all("<B1-Motion>")
            self.panel.unbind_all("<ButtonRelease-1>")
        except tk.TclError:
            pass
        try:
            self.panel.unbind("<Button-1>")
        except tk.TclError:
            pass
        
        # 销毁简洁视图
        if self._compact_root is not None:
            try:
                self._compact_root.destroy()
            except Exception:
                pass
        self._compact_root = None
        self._compact_card = None
        self._compact_cover = None
        self._compact_title = None
        self._compact_artist = None
        self._compact_ctl = None
        self._compact_prev = None
        self._compact_play = None
        self._compact_next = None
        self._compact_restore = None

        # 取消无边框 + 置顶
        try:
            self.panel.overrideredirect(False)
            self.panel.attributes("-topmost", False)
        except tk.TclError:
            pass

        # 恢复 head（用原始硬编码 pack 参数，避免 pack_info 类型问题）
        try:
            self.head.pack(fill="x", padx=10, pady=(10, 4))
        except Exception:
            pass

        # 恢复 Notebook
        try:
            self.nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        except Exception:
            pass

        # 恢复 btn_group
        try:
            self._btn_group_widget.place(relx=1.0, x=-8, y=10, anchor="ne")
        except Exception:
            pass

        # 恢复 f1 内被隐藏的部分（保留 card）
        try:
            self.lbl_time.pack(anchor="w")
        except Exception:
            pass
        try:
            self.progress.pack(fill="x", pady=(4, 0))
        except Exception:
            pass
        try:
            self._ctl.pack(pady=(10, 6))
        except Exception:
            pass
        try:
            self._vol_row.pack(fill="x", pady=(0, 10))
        except Exception:
            pass
        if getattr(self, "_lyrics_appearance_visible", False):
            try:
                self.ap_container.pack(fill="x")
            except Exception:
                pass

        # 强制重绘 head 里的 Canvas 按钮
        try:
            self._update_custom_widgets(self.head)
        except Exception:
            pass

        # 恢复几何
        try:
            geo = self._compact_saved.get("geometry", "500x365")
            self.panel.geometry(geo)
        except Exception:
            pass
                
        try:
            self._on_tab_changed()
        except Exception:
            pass
        try:
            self.panel.after(50, self._apply_titlebar_theme)
        except tk.TclError:
            pass
        
    def _bind_compact_drag(self):
        self._drag_data = {"x": 0, "y": 0}

        drag_exclude = set()
        for b in (getattr(self, "_compact_prev", None),
                  getattr(self, "_compact_play", None),
                  getattr(self, "_compact_next", None),
                  getattr(self, "_compact_restore", None),
                  getattr(self, "_compact_ctl", None)):
            if b is not None:
                drag_exclude.add(b)

        def on_drag(e):
            if not self._compact_mode:
                return
            dx = e.x_root - self._drag_data["x"]
            dy = e.y_root - self._drag_data["y"]
            x = self.panel.winfo_x() + dx
            y = self.panel.winfo_y() + dy
            self.panel.geometry(f"+{x}+{y}")
            self._drag_data["x"] = e.x_root
            self._drag_data["y"] = e.y_root

        def on_release(_e):
            try:
                self.panel.unbind_all("<B1-Motion>")
                self.panel.unbind_all("<ButtonRelease-1>")
            except tk.TclError:
                pass

        def on_press(e):
            if not self._compact_mode:
                return
            self._drag_data["x"] = e.x_root
            self._drag_data["y"] = e.y_root
            # 按下时全局捕获 Motion / Release，
            # 这样即使鼠标移出按下时所在的控件，拖拽也不会中断
            try:
                self.panel.bind_all("<B1-Motion>", on_drag)
                self.panel.bind_all("<ButtonRelease-1>", on_release)
            except tk.TclError:
                pass

        def walk(w):
            if w in drag_exclude:
                return
            try:
                w.bind("<Button-1>", on_press, add="+")
            except tk.TclError:
                pass
            for c in w.winfo_children():
                walk(c)

        if self._compact_root is not None:
            walk(self._compact_root)
            try:
                self.panel.bind("<Button-1>", on_press, add="+")
            except tk.TclError:
                pass
    def _toggle_lyrics(self):
        try:
            if self.overlay.is_visible():
                self.overlay.hide()
                self.btn_lyrics.set_text("♩")   # 空心
                self.cfg.OVERLAY_VISIBLE = False
            else:
                self.overlay.show()
                self.btn_lyrics.set_text("♪")   # 实心
                self.cfg.OVERLAY_VISIBLE = True
            self.cfg.save()
        except Exception:
            traceback.print_exc()            
       
    def _pl_load_tracks(self, pl_name):
        pl_name = str(pl_name or "").strip()
        try:
            self.pl_tracks_tree.delete(*self.pl_tracks_tree.get_children())
        except Exception:
            pass
        self._pl_tracks.clear()

        playlists = self._get_playlists_for_current()
        seen_ids = set()
        for pl in playlists:
            if not isinstance(pl, dict):
                continue
            if str(pl.get("name", "")).strip() != pl_name:
                continue
            tracks = pl.get("tracks", []) or []
            for t in tracks:
                if not isinstance(t, dict):
                    continue
                tid = str(t.get("id", "") or "")
                if not tid or tid in seen_ids:
                    continue
                seen_ids.add(tid)
                title = t.get("title", "") or ""
                artist = t.get("artist", "") or ""
                album = t.get("album", "") or ""
                disp = title
                if artist:
                    disp = f"{title} - {artist}"
                label = f"{len(seen_ids)}. {disp}"
                iid = f"plt{tid}_{len(seen_ids) - 1}"
                try:
                    self.pl_tracks_tree.insert("", "end", iid=iid, text=label)
                    self._pl_tracks[iid] = (tid, title, artist, album,
                                            len(seen_ids) - 1)
                except Exception:
                    pass
            # ★ 不 break：兼容历史遗留的"两条同名歌单"数据          

    def _fav_sync_selection(self):
        """收藏页：正在播放的曲目若在收藏列表中，自动高亮它。
           · 仅在收藏标签页生效
           · 只在 track_id 变化时写一次 selection
           · 播放自动切到下一曲时跟随高亮
           · 从收藏页激活播放后的短窗口内（poll 未更新前）跳过同步，
             避免覆盖用户刚点击选中的曲目"""
        try:
            if self.nb.index(self.nb.select()) != 3:
                return
        except (tk.TclError, ValueError):
            return
        try:
            if not self.fav_tree.get_children():
                return
        except tk.TclError:
            return
        if not self._fav_tracks:
            return

        ti = self.track_info or {}
        cur_tid = str(ti.get("track_id") or "")
        if not cur_tid:
            return

        # 等待 poll 把 track_info 更新到用户刚激活的曲目
        if self._fav_pending_track:
            if cur_tid != self._fav_pending_track:
                return
            self._fav_pending_track = None

        # 同一 track_id 不重复写 selection
        if self._fav_last_synced_track == cur_tid:
            return

        # 在收藏列表中找对应 iid（注意 iid 可能是 fav{tid} 或 fav{tid}_{idx}）
        target_iid = None
        for iid, info in self._fav_tracks.items():
            try:
                if str(info[0]) == cur_tid:
                    target_iid = iid
                    break
            except Exception:
                continue
        if not target_iid:
            return

        self._fav_last_synced_track = cur_tid

        try:
            cur_sel = self.fav_tree.selection()
            if cur_sel and cur_sel[0] == target_iid:
                return
            self.fav_tree.selection_set(target_iid)
            self.fav_tree.focus(target_iid)
            self.fav_tree.see(target_iid)
        except tk.TclError:
            pass        

    def _hide_tooltip(self):
        """销毁当前悬浮气泡。"""
        w = getattr(self, "_tooltip_win", None)
        if w is not None:
            try:
                w.destroy()
            except tk.TclError:
                pass
            self._tooltip_win = None

    def _show_tooltip(self, widget, text):
        """在 widget 下方显示「上方带小三角」的对话气泡。"""
        self._hide_tooltip()
        if not text:
            return
        try:
            widget.update_idletasks()
        except tk.TclError:
            return
        wx = widget.winfo_rootx() + widget.winfo_width() // 2
        wy = widget.winfo_rooty() + widget.winfo_height()

        # 量文字宽
        try:
            f = tkfont.Font(family="Microsoft YaHei UI", size=9)
            tw = f.measure(text) + 20
        except tk.TclError:
            f = None
            tw = len(text) * 8 + 20
        th = 22
        tri_h = 6
        tri_w = 10
        W = max(40, tw + 4)
        H = th + tri_h + 4

        tip = tk.Toplevel(self.panel)
        tip.wm_overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass

        key_color = "#FF00FF"
        try:
            tip.configure(bg=key_color)
            tip.attributes("-transparentcolor", key_color)
        except tk.TclError:
            pass

        c = tk.Canvas(tip, width=W, height=H, bg=key_color,
                      highlightthickness=0, bd=0)
        c.pack()

        # 气泡主体：圆角矩形
        x0, y0 = 2, tri_h + 2
        x1, y1 = W - 2, H - 2
        r = 6
        points = [
            x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
            x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
            x0, y1, x0, y1 - r, x0, y0 + r, x0, y0,
        ]
        c.create_polygon(points, smooth=True,
                         fill=self.CARD,
                         outline=self.SEL_BG, width=1)

        # 上方小三角（指向上）
        cx = W // 2
        c.create_polygon(
            cx - tri_w // 2, tri_h + 2,
            cx + tri_w // 2, tri_h + 2,
            cx, 2,
            fill=self.CARD, outline=self.SEL_BG, width=1)
        # 覆盖三角与矩形之间那条边
        c.create_polygon(
            cx - tri_w // 2 + 1, tri_h + 2,
            cx + tri_w // 2 - 1, tri_h + 2,
            cx, 3,
            fill=self.CARD, outline="")

        # 文字
        c.create_text(W // 2, tri_h + 2 + th // 2, text=text,
                      fill=self.FG,
                      font=("Microsoft YaHei UI", 9))

        try:
            tip.wm_geometry(f"+{wx - W // 2}+{wy}")
        except tk.TclError:
            pass

        self._tooltip_win = tip

    def _bind_tooltip(self, widget, text, delay=500):
        """给任意控件绑定悬浮气泡提示。
           text 可以是字符串，也可以是 callable（返回字符串，用于动态文字）。"""
        state = {"after_id": None}

        def cancel():
            aid = state["after_id"]
            if aid is not None:
                try:
                    self.panel.after_cancel(aid)
                except tk.TclError:
                    pass
                state["after_id"] = None
            self._hide_tooltip()

        def show():
            try:
                txt = text() if callable(text) else text
            except Exception:
                txt = ""
            if txt:
                self._show_tooltip(widget, txt)

        def on_enter(_e=None):
            cancel()
            try:
                state["after_id"] = self.panel.after(delay, show)
            except tk.TclError:
                pass

        def on_leave(_e=None):
            cancel()

        widget.bind("<Enter>", on_enter, add="+")
        widget.bind("<Leave>", on_leave, add="+")
        widget.bind("<ButtonPress>", on_leave, add="+")
        widget.bind("<Destroy>", lambda _e: cancel(), add="+")        