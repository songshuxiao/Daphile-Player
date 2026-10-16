# -*- coding: utf-8 -*-
"""控制面板：正在播放 / 音乐库 / 歌词外观设置（深色主题）
   · 可拖动进度条
   · 上/下一曲走 lms.prev_track / next_track（同专辑→同歌手→队列顺序）
   · 字体/颜色修改即时保存到 user-config.json
   · 正在播放卡片右侧显示专辑封面（无封面显示默认唱片图标）
   · 音乐库中歌手行带话筒图标，专辑行带唱片图标（Pillow 绘制，字体无关）"""

import threading
import time
import traceback
import urllib.request
from io import BytesIO
import tkinter as tk
from tkinter import ttk, colorchooser, font as tkfont

from lrc_parser import LrcParser

try:
    from PIL import Image, ImageDraw, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class ControlPanel:

    BG     = "#15151E"   # 面板底
    CARD   = "#20202E"   # 卡片底
    FIELD  = "#1A1A24"   # 输入控件底（近黑）
    FG     = "#E8E8F0"   # 主文字
    DIM    = "#9A9AB0"   # 次要文字
    SEL_BG = "#3D3D5C"   # 选中底
    ACCENT = "#5A5AE0"   # 强调色

    def __init__(self, lms, overlay, fetcher, cfg):
        self.lms = lms
        self.overlay = overlay
        self.fetcher = fetcher
        self.cfg = cfg

        self.track_info = {}
        self.song_pos = 0.0
        self._duration = 0.0
        self._seeking = False
        self.lines = []
        self.cur_line_idx = -1
        self._lyrics_source = ""
        self._fetch_token = 0
        self._playing = False
        self.player_name = ""
        self._vol_set = False

        # 专辑封面相关
        self._cover_cache = {}          # cover_id -> PhotoImage
        self._current_cover_id = ""
        self._cover_photo = None        # 保持引用，防 GC

        # 音乐库图标缓存（歌手 / 专辑）
        self._icon_cache = {}

        self._lib_items = {}
        self._lib_stack = []

        self.panel = tk.Toplevel(overlay.root)
        self.panel.title("LMS 控制面板")
        self.panel.geometry("500x450")
        self.panel.configure(bg=self.BG)

        self._build_style()
        self._build_ui()
        self._sync_font_controls()

        self._stop = False
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self._ui_tick_loop()

    # ================= 样式 =================
    def _build_style(self):
        style = ttk.Style(self.panel)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=self.BG, foreground=self.FG,
                        fieldbackground=self.FIELD, borderwidth=0)

        style.configure("TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.CARD)
        style.configure("TLabel", background=self.BG, foreground=self.FG)
        style.configure("Card.TLabel", background=self.CARD, foreground=self.FG)
        style.configure("Dim.TLabel", background=self.BG, foreground=self.DIM)

        # 按钮
        style.configure("TButton", background=self.CARD, foreground=self.FG,
                        padding=(8, 4))
        style.map("TButton",
                  background=[("active", self.SEL_BG), ("pressed", self.ACCENT)],
                  foreground=[("active", self.FG)])

        # Notebook / Tab
        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=self.CARD, foreground=self.FG,
                        padding=(15, 6), font=("Microsoft YaHei UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", self.SEL_BG)],
                  foreground=[("selected", "#FFFFFF")],
                  padding=[("selected", (17, 8)), ("!selected", (15, 6))],
                  expand=[("selected", [1, 0, 1, 0])])

        # Combobox：readonly 状态必须 map 单独映射，否则白底
        style.configure("TCombobox", fieldbackground=self.FIELD,
                        foreground=self.FG, background=self.CARD,
                        arrowcolor=self.FG)
        style.map("TCombobox",
                  fieldbackground=[("readonly", self.FIELD),
                                   ("active", self.FIELD)],
                  foreground=[("readonly", self.FG)],
                  selectbackground=[("readonly", self.FIELD)],
                  selectforeground=[("readonly", self.FG)])

        # Entry：全状态深底
        style.configure("TEntry", fieldbackground=self.FIELD,
                        foreground=self.FG, insertcolor=self.FG)
        style.map("TEntry",
                  fieldbackground=[("disabled", self.FIELD),
                                   ("readonly", self.FIELD),
                                   ("focus", self.FIELD),
                                   ("active", self.FIELD)],
                  foreground=[("disabled", self.FG), ("readonly", self.FG),
                              ("focus", self.FG)])

        # Scale（音量/进度条）
        style.configure("Horizontal.TScale", background=self.CARD,
                        troughcolor=self.FIELD)

        # Treeview（音乐库列表）
        style.configure("Treeview", background=self.FIELD, foreground=self.FG,
                        fieldbackground=self.FIELD, rowheight=26, borderwidth=0)
        style.map("Treeview",
                  background=[("selected", self.SEL_BG)],
                  foreground=[("selected", "#FFFFFF")])
        style.configure("Treeview.Heading", background=self.CARD,
                        foreground=self.DIM, relief="flat")

        # Scrollbar
        style.configure("Vertical.TScrollbar", background=self.CARD,
                        troughcolor=self.BG, arrowcolor=self.FG)

    # ================= UI 构建 =================
    def _build_ui(self):
        p = self.panel
        # 弹出列表配色（下拉框 / 列表框）
        p.option_add("*TCombobox*Listbox.background", self.FIELD)
        p.option_add("*TCombobox*Listbox.foreground", self.FG)
        p.option_add("*TCombobox*Listbox.selectBackground", self.SEL_BG)
        p.option_add("*TCombobox*Listbox.selectForeground", self.FG)
        p.option_add("*Listbox.background", self.FIELD)
        p.option_add("*Listbox.foreground", self.FG)
        p.option_add("*Listbox.selectBackground", self.SEL_BG)
        p.option_add("*Listbox.selectForeground", "#FFFFFF")

        # ---- 顶栏 ----
        head = tk.Frame(p, bg=self.BG)
        head.pack(fill="x", padx=10, pady=(10, 4))
        self.lbl_server = tk.Label(head, text="服务器: 搜索中…", bg=self.BG,
                                   fg=self.DIM, font=("Microsoft YaHei UI", 9))
        self.lbl_server.pack(side="left")
        self.lbl_player = tk.Label(head, text="", bg=self.BG, fg=self.DIM,
                                   font=("Microsoft YaHei UI", 9))
        self.lbl_player.pack(side="right")

        nb = ttk.Notebook(p)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.nb = nb

        # ================= TAB 1：正在播放 =================
        f1 = ttk.Frame(nb, padding=10)
        nb.add(f1, text="正在播放 ")

        card = tk.Frame(f1, bg=self.CARD)
        card.pack(fill="x", pady=(0, 8))

        # 右侧封面区（固定 72×72）
        self.cover_canvas = tk.Canvas(card, width=72, height=72,
                                      bg=self.CARD, highlightthickness=0, bd=0)
        self.cover_canvas.pack(side="right", padx=(0, 12), pady=10)

        # 左侧文字区
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

        # 初始显示默认唱片图标
        self._draw_default_cover()

        self.lbl_time = tk.Label(f1, text="00:00 / 00:00", bg=self.BG,
                                 fg=self.DIM, font=("Consolas", 10))
        self.lbl_time.pack(anchor="w")

        # ---- 进度条（可拖动） ----
        self.progress = ttk.Scale(f1, from_=0, to=100, orient="horizontal")
        self.progress.pack(fill="x", pady=(4, 0))
        self.progress.bind("<ButtonPress-1>", lambda _e: self._seek_start())
        self.progress.bind("<ButtonRelease-1>", self._seek_end)

        ctl = tk.Frame(f1, bg=self.BG)
        ctl.pack(fill="x", pady=6)
        self.btn_prev = self._mk_btn(ctl, "⏮", lambda: self._cmd("prev"))
        self.btn_play = self._mk_btn(ctl, "▶", self._toggle_play, width=5)
        self.btn_next = self._mk_btn(ctl, "⏭", lambda: self._cmd("next"))
        self.btn_prev.pack(side="left", padx=(0, 6))
        self.btn_play.pack(side="left", padx=6)
        self.btn_next.pack(side="left", padx=6)

        vol_row = tk.Frame(f1, bg=self.BG)
        vol_row.pack(fill="x", pady=(0, 10))
        tk.Label(vol_row, text="音量", bg=self.BG, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.scl_vol = ttk.Scale(vol_row, from_=0, to=100, orient="horizontal",
                                 command=self._on_volume)
        self.scl_vol.pack(side="left", fill="x", expand=True, padx=8)

        # ---- 歌词外观 ----
        ap = tk.LabelFrame(f1, text=" 歌词外观 ", bg=self.CARD, fg=self.FG)
        ap.pack(fill="x")

        r0 = tk.Frame(ap, bg=self.CARD)
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

        r1 = tk.Frame(ap, bg=self.CARD)
        r1.pack(fill="x", padx=10, pady=(4, 10))
        tk.Label(r1, text="已唱色", bg=self.CARD, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.btn_c_active = tk.Button(r1, width=6, relief="flat",
                                      bg=self.cfg.COLOR_ACTIVE,
                                      activebackground=self.SEL_BG,
                                      command=lambda: self._pick_color("COLOR_ACTIVE"))
        self.btn_c_active.pack(side="left", padx=4)
        tk.Label(r1, text="未唱色", bg=self.CARD, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(16, 0))
        self.btn_c_idle = tk.Button(r1, width=6, relief="flat",
                                    bg=self.cfg.COLOR_IDLE,
                                    activebackground=self.SEL_BG,
                                    command=lambda: self._pick_color("COLOR_IDLE"))
        self.btn_c_idle.pack(side="left", padx=4)
        tk.Label(r1, text="描边色", bg=self.CARD, fg=self.DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(16, 0))
        self.btn_c_stroke = tk.Button(r1, width=6, relief="flat",
                                      bg=self.cfg.COLOR_STROKE,
                                      activebackground=self.SEL_BG,
                                      command=lambda: self._pick_color("COLOR_STROKE"))
        self.btn_c_stroke.pack(side="left", padx=4)

        # ================= TAB 2：音乐库 =================
        f2 = ttk.Frame(nb, padding=10)
        nb.add(f2, text="音乐库")

        top = tk.Frame(f2, bg=self.BG)
        top.pack(fill="x", pady=(0, 6))
        self.btn_back = self._mk_btn(top, "←", self._lib_back)
        self.btn_back.pack(side="left", padx=(0, 6))
        self.ent_search = tk.Entry(top, bg=self.FIELD, fg=self.FG,
                                   insertbackground=self.FG, relief="flat",
                                   highlightthickness=1,
                                   highlightbackground=self.SEL_BG,
                                   highlightcolor=self.ACCENT)
        self.ent_search.pack(side="left", fill="x", expand=True, ipady=4, padx=4)
        self.ent_search.bind("<Return>", lambda _e: self._lib_search())
        self.btn_search = self._mk_btn(top, "⌕", self._lib_search)
        self.btn_search.pack(side="left", padx=(4, 0))

        mid = tk.Frame(f2, bg=self.BG)
        mid.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(mid, orient="vertical")
        self.tree = ttk.Treeview(mid, show="tree", selectmode="extended",
                                 yscrollcommand=sb.set)
        sb.config(command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self._lib_activate)

        bot = tk.Frame(f2, bg=self.BG)
        bot.pack(fill="x", pady=(6, 0))
        self.lbl_lib = tk.Label(bot, text="", bg=self.BG, fg=self.DIM,
                                font=("Microsoft YaHei UI", 9))
        self.lbl_lib.pack(side="left", padx=0)

        self._lib_show_artists()

    def _mk_btn(self, parent, text, cmd, width=None):
        kw = dict(text=text, command=cmd, bg=self.CARD, fg=self.FG,
                  activebackground=self.SEL_BG, activeforeground=self.FG,
                  relief="flat", font=("Microsoft YaHei UI", 10), padx=8, pady=3)
        if width:
            kw["width"] = width
        return tk.Button(parent, **kw)

    # ================= 播放控制 =================
    def _cmd(self, what):
        try:
            if what == "prev":
                self.lms.prev_track()
            elif what == "next":
                self.lms.next_track()
        except Exception:
            traceback.print_exc()

    def _toggle_play(self):
        try:
            self.lms.command(["pause"])
        except Exception:
            traceback.print_exc()

    def _on_volume(self, val):
        try:
            self.lms.command(["mixer", "volume", str(int(float(val)))])
        except Exception:
            pass

    # ---- 进度条拖动 ----
    def _seek_start(self):
        self._seeking = True

    def _seek_end(self, _e=None):
        if not self._seeking:
            return
        self._seeking = False
        try:
            pos = float(self.progress.get())
            if self._duration:
                self.lms.seek(pos)
                self.song_pos = pos
        except Exception:
            traceback.print_exc()

    # ================= 外观设置（即时保存） =================
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
        self.overlay.set_line(self._line_text(), self._next_text(),
                              self.track_info.get("title", ""))

    def _pick_color(self, key):
        current = getattr(self.cfg, key)
        _rgb, hexcolor = colorchooser.askcolor(color=current, parent=self.panel,
                                               title="选择歌词颜色")
        if hexcolor:
            setattr(self.cfg, key, hexcolor)
            btn_map = {
                "COLOR_ACTIVE": self.btn_c_active,
                "COLOR_IDLE":   self.btn_c_idle,
                "COLOR_STROKE": self.btn_c_stroke,
            }
            btn = btn_map.get(key)
            if btn:
                btn.config(bg=hexcolor)
            self.cfg.save()
            self.overlay.set_line(self._line_text(), self._next_text(),
                                  self.track_info.get("title", ""))

    # ================= 状态轮询 =================
    def _poll_loop(self):
        last_track = None
        while not self._stop:
            try:
                st = self.lms.get_status()
                self.song_pos = st.get("time", 0.0)
                self._duration = st.get("duration", 0.0)
                self._playing = st.get("mode") == "play"
                if not self._vol_set and "volume" in st:
                    try:
                        self.scl_vol.set(float(st["volume"]))
                        self._vol_set = True
                    except (TypeError, ValueError):
                        pass
                tid = st.get("track_id")
                if tid != last_track:
                    last_track = tid
                    self.track_info = st
                    self._start_fetch(st)
                # ★ 每次轮询都检查封面（cover_id 相同会内部跳过）
                self._update_cover(st)
            except Exception:
                time.sleep(1.0)
                continue
            time.sleep(self.cfg.POLL_INTERVAL)

    def _start_fetch(self, st):
        self.lines = []
        self.cur_line_idx = -1
        self._lyrics_source = ""
        token = self._fetch_token = self._fetch_token + 1
        track_id = st.get("track_id")
        if not track_id:
            self._lyrics_source = "未找到"
            return

        def work():
            try:
                src, lrc = self.fetcher.fetch(
                    track_id, st.get("artist", ""), st.get("title", ""),
                    st.get("album", ""), st.get("duration", 0))
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
            self.panel.after(0, apply)

        threading.Thread(target=work, daemon=True).start()

    # ================= 专辑封面 =================
    def _draw_default_cover(self):
        """无封面时：画一个唱片图标"""
        c = self.cover_canvas
        c.delete("all")
        cx = cy = 36
        c.create_oval(cx - 32, cy - 32, cx + 32, cy + 32,
                      fill="#2A2A3A", outline="#3D3D5C", width=2)
        c.create_oval(cx - 14, cy - 14, cx + 14, cy + 14,
                      fill=self.ACCENT, outline="")
        c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3,
                      fill=self.CARD, outline="")
        c.create_arc(cx - 26, cy - 26, cx + 26, cy + 26, start=30, extent=60,
                     style="arc", outline="#4A4A6A", width=2)
        self._cover_photo = None

    def _set_cover_image(self, photo):
        """主线程中显示封面图"""
        c = self.cover_canvas
        c.delete("all")
        self._cover_photo = photo
        c.create_image(36, 36, image=photo)

    def _update_cover(self, st):
        """根据 status 里的封面标识决定显示封面 / 默认图标（在 poll 线程调用）"""
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
        """后台下载封面字节，回主线程转 PhotoImage"""
        host, port = self.lms.host, self.lms.port
        mac = ""
        try:
            mac = self.lms.player_mac() or ""
        except Exception:
            pass

        urls = []
        if cover_id and cover_id not in ("0", "-"):
            urls += [
                f"http://{host}:{port}/music/{cover_id}/cover.jpg",
                f"http://{host}:{port}/music/{cover_id}/cover_100x100.jpg",
                f"http://{host}:{port}/music/{cover_id}/cover_72x72.jpg",
            ]
        if track_id:
            urls += [
                f"http://{host}:{port}/music/{track_id}/cover.jpg",
                f"http://{host}:{port}/music/{track_id}/cover_100x100.jpg",
                f"http://{host}:{port}/music/{track_id}/cover_72x72.jpg",
            ]
        if mac:
            urls.append(f"http://{host}:{port}/music/current/cover.jpg?player={mac}")

        def is_image(data):
            if not data or len(data) < 8:
                return False
            return (data[:3] == b"\xff\xd8\xff"
                    or data[:8] == b"\x89PNG\r\n\x1a\n"
                    or data[:6] in (b"GIF87a", b"GIF89a")
                    or data[:2] == b"BM")

        def work():
            for url in urls:
                try:
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "lms-karaoke/1.0"})
                    with urllib.request.urlopen(req, timeout=6) as r:
                        data = r.read()
                    if is_image(data):
                        print(f"[封面] 命中: {url} ({len(data)} bytes)")
                        self.panel.after(
                            0, lambda d=data, k=key: self._on_cover_downloaded(k, d))
                        return
                    else:
                        print(f"[封面] 非图片响应: {url} "
                              f"(head={data[:16]!r})")
                except Exception as e:
                    print(f"[封面] 请求失败 {url}: {e}")
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
            print(f"[封面] 已显示 key={key}")
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

    def _ui_tick(self):
        info = self.track_info
        title = info.get("title", "") or "—"
        artist = " / ".join(x for x in (info.get("artist", ""),
                                        info.get("album", "")) if x)
        self.lbl_title.config(text=title)
        self.lbl_artist.config(text=artist)
        self.lbl_time.config(text=f"{self._fmt(self.song_pos)} / "
                                  f"{self._fmt(self._duration)}")
        self.btn_play.config(text="⏸" if self._playing else "▶")
        if self.player_name:
            self.lbl_player.config(text=f"播放器: {self.player_name}")

        if not self._seeking:
            try:
                self.progress.config(to=max(self._duration, 1.0))
                self.progress.set(min(self.song_pos,
                                      self._duration or self.song_pos))
            except Exception:
                pass

        # 卡拉OK刷写
        if self.lines:
            idx = LrcParser.find_current(self.lines, self.song_pos)
            if idx != self.cur_line_idx:
                self.cur_line_idx = idx
                self.overlay.set_line(self._line_text(), self._next_text(),
                                      info.get("title", ""))
            if 0 <= idx < len(self.lines):
                t0 = self.lines[idx][0]
                t1 = (self.lines[idx + 1][0] if idx + 1 < len(self.lines)
                      else t0 + max(3.0, len(self.lines[idx][1]) * 0.28))
                span = max(t1 - t0, 0.3)
                self.overlay.set_progress((self.song_pos - t0) / span)
        elif self._lyrics_source == "未找到":
            self.overlay.clear(info.get("title", ""),
                               message=f"暂无歌词：{info.get('title', '')}")
        else:
            t = info.get("title", "")
            self.overlay.clear(t, message=f"{t}" if t else "")

    @staticmethod
    def _fmt(sec):
        try:
            sec = int(float(sec))
        except (TypeError, ValueError):
            sec = 0
        return f"{sec // 60:02d}:{sec % 60:02d}"

    def _line_text(self):
        if 0 <= self.cur_line_idx < len(self.lines):
            return self.lines[self.cur_line_idx][1]
        return ""

    def _next_text(self):
        i = self.cur_line_idx + 1
        if 0 <= i < len(self.lines):
            return self.lines[i][1]
        return ""

    # ================= 音乐库 =================
    def _lib_set_status(self, text):
        self.lbl_lib.config(text=text)

    def _lib_clear(self):
        self.tree.delete(*self.tree.get_children())
        self._lib_items.clear()

    # ---- 图标生成 ----
    def _make_icon(self, kind):
        """生成 18×18 图标：'artist'=话筒，'album'=唱片。
           结果缓存，多次调用只画一次。无 PIL 时返回 None。"""
        if kind in self._icon_cache:
            return self._icon_cache[kind]
        if not HAS_PIL:
            self._icon_cache[kind] = None
            return None

        size = 18
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        accent = self.ACCENT
        dark = "#3D3D5C"

        if kind == "artist":
            # 话筒：网罩 + 把手
            d.ellipse((6, 1, 12, 10), fill=accent)          # 网罩
            d.ellipse((7, 2, 9, 6), fill="#FFFFFF")          # 高光
            d.rectangle((8, 10, 10, 15), fill=dark)          # 把手
            d.rectangle((6, 15, 12, 16), fill=dark)          # 底座
        elif kind == "album":
            # 唱片：黑胶盘 + 标签 + 中心孔
            d.ellipse((0, 0, 17, 17), fill=dark)
            d.ellipse((4, 4, 13, 13), fill=accent)
            d.ellipse((7, 7, 10, 10), fill="#15151E")
            d.arc((1, 1, 16, 16), start=200, end=280,
                  fill="#7A7AA0", width=1)

        try:
            photo = ImageTk.PhotoImage(img)
        except Exception:
            photo = None
        self._icon_cache[kind] = photo
        return photo

    def _lib_add_row(self, iid, text, kind, item_id, extra=None):
        """kind='artist'/'album' 时自动带图标；'track' 不带图标"""
        icon = None
        if kind in ("artist", "album"):
            icon = self._make_icon(kind)
        if icon is not None:
            self.tree.insert("", "end", iid=iid, text=text, image=icon)
        else:
            self.tree.insert("", "end", iid=iid, text=text)
        self._lib_items[iid] = (kind, str(item_id), text, extra)

    def _lib_run(self, status_text, work, fill):
        self._lib_set_status(status_text)

        def thread_body():
            try:
                data = work()
            except Exception as e:
                print(f"[音乐库] {e}")
                data = []
            self.panel.after(0, lambda: fill(data))

        threading.Thread(target=thread_body, daemon=True).start()

    def _lib_show_artists(self):
        self._lib_stack = []
        self._lib_run("加载艺人中…",
                      self.lms.browse_artists,
                      self._lib_fill_artists)

    def _lib_fill_artists(self, artists):
        self._lib_clear()
        for a in artists:
            self._lib_add_row(f"ar{a[0]}", f"{a[1]}", "artist", a[0])
        self._lib_set_status(f"共 {len(artists)} 位艺人 · 双击浏览专辑")

    def _lib_browse_albums(self, artist_id, artist_name):
        self._lib_run(f"加载 {artist_name} 的专辑…",
                      lambda: self.lms.browse_albums(artist_id),
                      lambda data: self._lib_fill_albums(data, artist_name))

    def _lib_fill_albums(self, albums, artist_name):
        self._lib_clear()
        for al in albums:
            self._lib_add_row(f"al{al[0]}", f"{al[1]}", "album", al[0])
        self._lib_set_status(f"{artist_name} · {len(albums)} 张专辑 · 双击浏览歌曲")

    def _lib_browse_tracks(self, album_id, album_name):
        self._lib_run(f"加载 {album_name} 的歌曲…",
                      lambda: self.lms.browse_tracks(album_id),
                      lambda data: self._lib_fill_tracks(data, album_name))

    def _lib_fill_tracks(self, tracks, album_name):
        self._lib_clear()
        for t in tracks:
            tid, tno, tname = t
            label = f"{tno}. {tname}" if tno else tname
            self._lib_add_row(f"tr{tid}", label, "track", tid)
        self._lib_set_status(f"{album_name} · {len(tracks)} 首 · 双击播放")

    def _lib_search(self):
        q = self.ent_search.get().strip()
        if not q:
            return
        self._lib_run(f"搜索“{q}”…",
                      lambda: self.lms.search(q),
                      self._lib_fill_search)

    def _lib_fill_search(self, res):
        self._lib_clear()
        if isinstance(res, list):
            d = {"artists": [], "albums": [], "tracks": []}
            for item in res:
                if isinstance(item, dict) and "title" in item:
                    d["tracks"].append((item.get("id"), 0, item["title"]))
                elif isinstance(item, dict):
                    d["artists"].append((item.get("id"),
                                         item.get("name", str(item))))
            res = d
        if not isinstance(res, dict):
            self._lib_set_status("搜索结果格式异常")
            return

        n = 0
        for t in res.get("tracks", []) or []:
            self._lib_add_row(f"tr{t[0]}", f"{t[2]}", "track", t[0])
            n += 1
        for al in res.get("albums", []) or []:
            self._lib_add_row(f"al{al[0]}", f"{al[1]}", "album", al[0])
            n += 1
        for a in res.get("artists", []) or []:
            self._lib_add_row(f"ar{a[0]}", f"{a[1]}", "artist", a[0])
            n += 1
        self._lib_set_status(f"搜索到 {n} 项")

    def _lib_activate(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        kind, item_id, name, _extra = self._lib_items[sel[0]]
        if kind == "artist":
            self._lib_stack.append(("artist", item_id, name))
            self._lib_browse_albums(item_id, name)
        elif kind == "album":
            self._lib_stack.append(("album", item_id, name))
            self._lib_browse_tracks(item_id, name)
        elif kind == "track":
            self.lms.play_track(item_id)
            self._lib_set_status(f"正在播放: {name}")

    def _lib_back(self):
        if not self._lib_stack:
            # 已在顶层，刷新艺人列表
            self._lib_show_artists()
            return

        # 弹出"当前正在浏览的对象"，回到它的上一级
        self._lib_stack.pop()

        if not self._lib_stack:
            # 栈空 → 回顶层艺人列表
            self._lib_show_artists()
            return

        # 看新的栈顶，决定回到哪个视图
        kind, iid, name = self._lib_stack[-1]
        if kind == "artist":
            self._lib_browse_albums(iid, name)
        else:
            self._lib_browse_tracks(iid, name)

    # ================= 关闭 =================
    def close(self):
        self._stop = True
        self._cover_cache.clear()
        self._cover_photo = None
        self._icon_cache.clear()