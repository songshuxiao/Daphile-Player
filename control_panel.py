# -*- coding: utf-8 -*-
"""控制面板：正在播放 / 音乐库 / 歌词外观设置（深色/浅色主题切换）"""

import os
import sys
import json
import threading
import time
import traceback
import urllib.request
import webbrowser
import tkinter as tk
from tkinter import ttk, colorchooser, messagebox, font as tkfont
from io import BytesIO

from lrc_parser import LrcParser

try:
    from PIL import Image, ImageDraw, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


APP_VERSION = "v5.0.0"
APP_REPO = "songshuxiao/Daphile-Player"
GITHUB_URL = f"https://github.com/{APP_REPO}"
RELEASES_API = f"https://api.github.com/repos/{APP_REPO}/releases/latest"
DOWNLOAD_URL = "https://cloud.szfx.top/Windows/235.html"


def _resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


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

        # 键盘导航标志位
        self._kb_focus_albums = False
        self._kb_select_tracks = False

        self._update_checking = False
        self._about_status_lbl = None
        self._about_auto_toggle = None

        self.panel = tk.Toplevel(overlay.root)
        self.panel.title("达菲播放器")
        self.panel.geometry("500x365")
        self.panel.configure(bg=self.BG)

        self._build_style()
        self._build_ui()
        self._sync_font_controls()

        self.panel.update_idletasks()
        try:
            self.panel.geometry(f"500x{self._calc_panel_height()}")
        except tk.TclError:
            pass

        # 绑定键盘全局事件
        self.panel.bind("<KeyPress>", self._on_key_press)
        # ★ 点击面板上任意非输入控件时，主动把焦点从 Spinbox/Entry 移开
        self.panel.bind("<Button-1>", self._on_panel_click, add="+")

        self._stop = False
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self._ui_tick_loop()

        if getattr(self.cfg, "AUTO_CHECK_UPDATE", True):
            self.panel.after(3000, lambda: self._check_update(auto=True))

    # ================= 键盘快捷键 =================
    def _on_key_press(self, event):
        w = event.widget
        # 如果焦点在输入框/微调框/下拉框里，不拦截，让用户可以正常打字
        if isinstance(w, (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Combobox, tk.Text)):
            return None

        # Tab 切换标签页
        if event.keysym == "Tab":
            cur = self.nb.index(self.nb.select())
            new_tab = 1 if cur == 0 else 0
            self.nb.select(new_tab)
            if new_tab == 1:
                # 切到音乐库：焦点还给用户最后操作的那一列
                try:
                    self.panel.update_idletasks()
                    self._focus_library_tree()
                except tk.TclError:
                    pass
            else:
                # 切回播放中：把焦点从树移开，避免残留焦点干扰方向键
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
        style.configure("TNotebook.Tab", background=self.CARD, foreground=self.FG,
                        padding=(15, 6), font=("Microsoft YaHei UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", self.SEL_BG)],
                  foreground=[("selected", "#FFFFFF" if self.cfg.DARK_MODE else "#1A1A24")],
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

    # ================= UI 构建 =================
    def _build_ui(self):
        p = self.panel
        p.option_add("*TCombobox*Listbox.background", self.FIELD)
        p.option_add("*TCombobox*Listbox.foreground", self.FG)
        p.option_add("*TCombobox*Listbox.selectBackground", self.SEL_BG)
        p.option_add("*TCombobox*Listbox.selectForeground", self.FG)

        head = tk.Frame(p, bg=self.BG)
        head.pack(fill="x", padx=10, pady=(10, 4))

        self.lbl_server = tk.Label(head, text="服务器: 搜索中…", bg=self.BG,
                                   fg=self.ACCENT, font=("Microsoft YaHei UI", 9, "underline"),
                                   cursor="hand2")
        self.lbl_server.pack(side="left")
        self.lbl_server.bind("<Button-1>", self._open_lms_web)

        self.btn_daphile_settings = self._make_round_btn(
            head, "达菲设置", self._open_daphile_settings,
            width=64, height=22, radius=6)
        self.btn_daphile_settings.pack(side="left", padx=(10, 0))

        self.lbl_player = tk.Label(head, text="播放器: … ▼", bg=self.BG, fg=self.DIM,
                                   font=("Microsoft YaHei UI", 9), cursor="hand2")
        self.lbl_player.pack(side="right")
        self.lbl_player.bind("<Button-1>", self._show_players_menu)

        nb = ttk.Notebook(p)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.nb = nb
        # ★ 在 widget 层提前拦截方向键，避免被 ttk.Notebook 的 class bind 抢先切标签
        nb.bind("<Left>", self._on_key_press)
        nb.bind("<Right>", self._on_key_press)

        btn_group = tk.Frame(nb, bg=self.BG)
        btn_group.place(relx=1.0, x=-8, y=10, anchor="ne")

        self.btn_settings = self._make_round_btn(
            btn_group, "⚙ 设置", self._toggle_lyrics_panel,
            width=64, height=22, radius=8)
        self.btn_settings.pack(side="left", padx=(0, 6))

        self.btn_lyrics = self._make_round_btn(
            btn_group, "● 歌词", self._toggle_lyrics,
            width=64, height=22, radius=8)
        self.btn_lyrics.pack(side="left", padx=(0, 6))

        self.btn_shutdown = self._make_round_btn(
            btn_group, "⏻ 达菲", self._shutdown_daphile,
            width=64, height=22, radius=8)
        self.btn_shutdown.pack(side="left")

        # ================= TAB 1：正在播放 =================
        f1 = ttk.Frame(nb, padding=10)
        nb.add(f1, text="播放中")

        card = tk.Frame(f1, bg=self.CARD)
        card.pack(fill="x", pady=(0, 8))

        self.cover_canvas = tk.Canvas(card, width=72, height=72,
                                      bg=self.CARD, highlightthickness=0, bd=0)
        self.cover_canvas.pack(side="right", padx=(0, 12), pady=10)

        info = tk.Frame(card, bg=self.CARD)
        info.pack(side="left", fill="both", expand=True, padx=12, pady=10)
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
        nb.add(f2, text="音乐库")

        top = tk.Frame(f2, bg=self.BG)
        top.pack(fill="x", pady=(0, 6))
        self.ent_search = tk.Entry(top, bg=self.FIELD, fg=self.FG,
                                   insertbackground=self.FG, relief="flat",
                                   highlightthickness=1,
                                   highlightbackground=self.SEL_BG,
                                   highlightcolor=self.ACCENT)
        self.ent_search.pack(side="left", fill="x", expand=True, ipady=4, padx=4)
        self.ent_search.bind("<Return>", lambda _e: self._lib_search())
        self.btn_search = self._mk_btn(top, "⌕", self._lib_search)
        self.btn_search.pack(side="left", padx=(4, 0))
        self.btn_refresh = self._mk_btn(top, "↻", self._lib_refresh)
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
        self.tree_tracks = self._build_tree_column(c3)

        c2.grid_remove()
        c3.grid_remove()
        cols.columnconfigure(0, weight=1)

        self.tree_artists.bind("<<TreeviewSelect>>", self._on_artist_selected)
        self.tree_albums.bind("<<TreeviewSelect>>", self._on_album_selected)
        self.tree_tracks.bind("<Double-1>", self._on_track_activate)

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

        bot = tk.Frame(f2, bg=self.BG)
        bot.pack(fill="x", pady=(6, 0))
        self.lbl_lib = tk.Label(bot, text="", bg=self.BG, fg=self.DIM,
                                font=("Microsoft YaHei UI", 9))
        self.lbl_lib.pack(side="left", padx=0)

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
            self.lbl_player.config(bg=self.BG, fg=self.DIM)
            self.lbl_hdr_artists.config(bg=self.BG, fg=self.DIM)
            self.lbl_hdr_albums.config(bg=self.BG, fg=self.DIM)
            self.lbl_hdr_tracks.config(bg=self.BG, fg=self.DIM)
            self.lbl_lib.config(bg=self.BG, fg=self.DIM)
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
            if what == "prev": self.lms.prev_track()
            elif what == "next": self.lms.next_track()
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

    def _toggle_lyrics(self):
        try:
            if self.overlay.is_visible():
                self.overlay.hide()
                self.btn_lyrics.set_text("○ 歌词")
                self.cfg.OVERLAY_VISIBLE = False
            else:
                self.overlay.show()
                self.btn_lyrics.set_text("● 歌词")
                self.cfg.OVERLAY_VISIBLE = True
            self.cfg.save()
        except Exception:
            traceback.print_exc()

    def _calc_panel_height(self):
        return 490 if self._lyrics_appearance_visible else 365

    def _on_tab_changed(self, _e=None):
        try:
            cur = self.nb.index(self.nb.select())
        except Exception:
            return

        if cur == 0:
            self.btn_settings.pack_forget()
            self.btn_settings.pack(side="left", padx=(0, 6), before=self.btn_lyrics)
            # ★ 切回播放中时主动把焦点移出树/Notebook，
            #   之后按方向键都会走到 _on_key_press
            try:
                self.panel.focus_set()
            except tk.TclError:
                pass        
        else:
            self.btn_settings.pack_forget()
            if not self.tree_artists.get_children():
                self._lib_show_artists()

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
            self.lbl_player.config(text="播放器: (无可用)", fg=self.DIM)
        elif not is_online:
            self.lbl_player.config(text=f"播放器: (离线){arrow}", fg="#FF8080")
        else:
            if self.player_name != current_name:
                self.player_name = current_name
            if new_added:
                self.lbl_player.config(text=f"播放器: {current_name} (新){arrow}", fg=self.ACCENT)
            else:
                self.lbl_player.config(text=f"播放器: {current_name}{arrow}", fg=self.DIM)

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
        self.lbl_player.config(text=f"播放器: {name} ▼", fg=self.DIM)

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
                        if messagebox.askyesno("发现新版本", f"当前版本 {APP_VERSION}\n发现新版 {latest}\n\n是否前往下载？", parent=self.panel):
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

    def _set_cover_image(self, photo):
        c = self.cover_canvas
        c.delete("all")
        self._cover_photo = photo
        c.create_image(36, 36, image=photo)

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
        self.lbl_time.config(text=f"{self._fmt(self.song_pos)} / {self._fmt(self._duration)}")
        self.btn_play.set_text("⏸" if self._playing else "▶")

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
            self.btn_lyrics.set_text("● 歌词" if vis else "○ 歌词")
            if self._last_overlay_vis is None:
                self._last_overlay_vis = vis
            elif self._last_overlay_vis != vis:
                self._last_overlay_vis = vis
                self.cfg.OVERLAY_VISIBLE = vis
                self.cfg.save()
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
        confirm = messagebox.askyesno("确认关机", "确定要关闭达菲设备吗？\n\n关机后需要手动开机或远程唤醒。", parent=self.panel)
        if not confirm: return

        self.btn_shutdown.set_state(False)
        self.btn_shutdown.set_text("正在关机…")
        self._lib_set_status("正在向达菲发送关机指令…")
        host = self.lms.host

        def work():
            ok = self.lms.shutdown_daphile()
            if not ok:
                def fail():
                    self._lib_set_status("关机指令发送失败，请检查网络")
                    self.btn_shutdown.set_text("⏻ 达菲")
                    self.btn_shutdown.set_state(True)
                self.panel.after(0, fail)
                return

            def sent():
                self._lib_set_status("关机指令已发送，等待设备断开…")
                self.btn_shutdown.set_text("等待关机…")
            self.panel.after(0, sent)

            time.sleep(2)
            down = self._wait_for_host_down(host, timeout=30)

            def apply():
                if down:
                    self._lib_set_status("设备已关机，即将返回连接界面…")
                    self.panel.after(500, self._after_shutdown)
                else:
                    self._lib_set_status("等待超时，设备可能仍在运行，请手动确认")
                    self.btn_shutdown.set_text("⏻ 达菲")
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

        try:
            self.btn_shutdown.set_text("⏻ 达菲")
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
                self.btn_lyrics.set_text("● 歌词")
            else:
                self.overlay.hide()
                self.btn_lyrics.set_text("○ 歌词")
        except Exception:
            pass
    
    def _get_circle_photo(self, diameter, color):
        """Pillow 超采样生成抗锯齿圆，按 (直径, 颜色) 缓存。"""
        if not hasattr(self, "_circle_cache"):
            self._circle_cache = {}
        key = (diameter, color)
        if key in self._circle_cache:
            return self._circle_cache[key]
        if not HAS_PIL:
            return None
        try:
            ss = 4
            big = Image.new("RGB", (diameter * ss, diameter * ss), self.BG)
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
    def _make_round_btn(self, parent, text, cmd, width=112, height=22, radius=8, bg=None, btn_bg=None):
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
            canvas.create_text(w // 2, h // 2, text=state["text"], fill=fg_color, font=("Microsoft YaHei UI", 9))

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