# -*- coding: utf-8 -*-
"""桌面卡拉OK歌词浮窗 v4：
   · 无背景蒙板：窗口背景为色键透明，只有文字像素可见
   · 抗锯齿毛边修复：Pillow 离屏渲染文字 + 1px 描边，一次成型无叠影；
     无 PIL 时自动回退到 Canvas 8 方向同色描边
   · 刷写式高亮：独立刷写层按进度从左到右裁剪显示高亮色
   · 左键拖动 / 右键菜单"""

import os
import tkinter as tk
from tkinter import font as tkfont

try:
    import winreg
except ImportError:
    winreg = None

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ---------- 字体文件查找 ----------
_FONT_CANDIDATES = {
    "microsoft yahei ui": ("msyh.ttc", "msyh.ttf"),
    "microsoft yahei":    ("msyh.ttc", "msyh.ttf"),
    "微软雅黑":             ("msyh.ttc", "msyh.ttf"),
    "simhei":             ("simhei.ttf",),
    "黑体":                ("simhei.ttf",),
    "simsun":             ("simsun.ttc",),
    "宋体":                ("simsun.ttc",),
    "kaiti":              ("simkai.ttf",),
    "楷体":                ("simkai.ttf",),
    "fangsong":           ("simfang.ttf",),
    "仿宋":                ("simfang.ttf",),
    "dengxian":           ("deng.ttf",),
    "dengxian light":     ("dengl.ttf",),
    "等线":                ("deng.ttf",),
    "等线 light":          ("dengl.ttf",),
    "consolas":           ("consola.ttf",),
    "segoe ui":           ("segoeui.ttf",),
    "arial":              ("arial.ttf",),
    "tahoma":             ("tahoma.ttf",),
    "calibri":            ("calibri.ttf",),
    "source han sans":    ("SourceHanSansSC-Regular.otf",
                           "SourceHanSansCN-Regular.otf",
                           "SourceHanSans-Regular.otf"),
    "source han sans cn": ("SourceHanSansSC-Regular.otf",
                           "SourceHanSansCN-Regular.otf"),
    "思源黑体":             ("SourceHanSansSC-Regular.otf",
                           "SourceHanSansCN-Regular.otf"),
    "思源黑体 cn":          ("SourceHanSansSC-Regular.otf",
                           "SourceHanSansCN-Regular.otf"),
    "source han serif":   ("SourceHanSerifSC-Regular.otf",),
    "思源宋体":             ("SourceHanSerifSC-Regular.otf",),
    "noto sans":          ("NotoSans-Regular.ttf",),
    "noto sans sc":       ("NotoSansSC-Regular.otf",),
}

_FALLBACK_FONTS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/deng.ttf",
    "C:/Windows/Fonts/arial.ttf",
)

_font_file_cache = {}
_pil_font_cache = {}
_REGISTRY_FONTS = None
_USER_FONT_SCAN = None

# 风格后缀（宽松匹配时剥离）
_STYLE_SUFFIXES = (
    " light", " bold", " regular", " italic", " medium",
    " semibold", " black", " thin", " heavy", " book",
    " ultralight", " extralight", " condensed",
)


def _norm_name(s):
    """归一化字体名：小写、去风格后缀、去空格/连字符/下划线"""
    if not s:
        return ""
    n = s.lower().strip()
    for suf in _STYLE_SUFFIXES:
        if n.endswith(suf):
            n = n[: -len(suf)]
            break
    return n.replace(" ", "").replace("-", "").replace("_", "")


def _font_dirs():
    dirs = []
    windir = os.environ.get("WINDIR") or "C:/Windows"
    dirs.append(os.path.join(windir, "Fonts"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    return [d for d in dirs if os.path.isdir(d)]


def _user_font_dir():
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    d = os.path.join(local, "Microsoft", "Windows", "Fonts")
    return d if os.path.isdir(d) else None


def _load_registry_fonts():
    """加载 HKLM + HKCU 的字体注册表，返回 {小写字体名: 文件路径}"""
    global _REGISTRY_FONTS
    if _REGISTRY_FONTS is not None:
        return _REGISTRY_FONTS
    result = {}
    if not winreg:
        _REGISTRY_FONTS = result
        return result
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(
                hive,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        except OSError:
            continue
        try:
            n = winreg.QueryInfoKey(key)[1]
            for i in range(n):
                try:
                    name, data, _ = winreg.EnumValue(key, i)
                except OSError:
                    continue
                # 值名形如 "DengXian Light (TrueType)" / "等线 Light (TrueType)"
                base = name.split("(")[0].strip().rstrip(",").strip()
                if not base:
                    continue
                if os.path.isabs(data):
                    p = data
                else:
                    p = os.path.join(
                        os.environ.get("WINDIR", "C:/Windows"),
                        "Fonts", data)
                if os.path.isfile(p):
                    result.setdefault(base.lower(), p)
        finally:
            try:
                winreg.CloseKey(key)
            except OSError:
                pass
    _REGISTRY_FONTS = result
    return result


def _scan_user_fonts():
    """用 Pillow 读取用户级字体目录中每个文件的真实 family/style 名。
       结果形如 {'等线 light': 'C:/Users/.../Dengl.ttf',
                 'dengxian light': ..., 'dengxian': ...}
       只扫描 %LOCALAPPDATA%\\Microsoft\\Windows\\Fonts（用户安装的第三方
       字体都在这），避免全盘枚举系统字体造成卡顿。"""
    global _USER_FONT_SCAN
    if _USER_FONT_SCAN is not None:
        return _USER_FONT_SCAN
    result = {}
    if not HAS_PIL:
        _USER_FONT_SCAN = result
        return result
    d = _user_font_dir()
    if not d:
        _USER_FONT_SCAN = result
        return result
    for fname in sorted(os.listdir(d)):
        fl = fname.lower()
        if not fl.endswith((".ttf", ".ttc", ".otf")):
            continue
        p = os.path.join(d, fname)
        # ttc 内可能含多个 face，逐个尝试
        for idx in range(20):
            try:
                f = ImageFont.truetype(p, 12, index=idx)
            except Exception:
                break
            try:
                fam, style = f.getname()
            except Exception:
                continue
            if not fam:
                continue
            # 登记 family 本身
            result.setdefault(fam.lower(), p)
            # 也登记 family + style 组合（如 "DengXian Light"）
            if style and style.lower() not in ("regular", "book"):
                result.setdefault(f"{fam} {style}".lower(), p)
            # 清空 Pillow 字体缓存，避免反复持有 ttc face
            try:
                del f
            except Exception:
                pass
    _USER_FONT_SCAN = result
    return result


def _find_font_file_impl(family):
    if not family:
        return None
    target = family.lower().strip()
    target_key = _norm_name(target)

    # ① 注册表精确匹配（HKLM + HKCU）
    reg = _load_registry_fonts()
    if target in reg:
        return reg[target]

    # ② 注册表宽松匹配（去掉风格后缀/空格）
    if target_key:
        for name, path in reg.items():
            if _norm_name(name) == target_key:
                return path

    # ③ 用户级字体扫描精确匹配（第三方字体走这条）
    scan = _scan_user_fonts()
    if target in scan:
        return scan[target]

    # ④ 用户级字体扫描宽松匹配
    if target_key:
        for name, path in scan.items():
            if _norm_name(name) == target_key:
                return path

    # ⑤ 候选表（硬编码常见字体）
    for d in _font_dirs():
        for c in _FONT_CANDIDATES.get(target, ()):
            fp = os.path.join(d, c)
            if os.path.isfile(fp):
                return fp

    # ⑥ 文件名模糊匹配
    if target_key:
        for d in _font_dirs():
            try:
                for fname in sorted(os.listdir(d)):
                    fl = fname.lower()
                    if not fl.endswith((".ttf", ".ttc", ".otf")):
                        continue
                    if target_key in _norm_name(fl):
                        return os.path.join(d, fname)
            except OSError:
                pass
    return None


def _find_font_file(family):
    if family not in _font_file_cache:
        path = _find_font_file_impl(family)
        if path:
            print(f"[浮窗] 字体 '{family}' → {path}")
        else:
            for cand in _FALLBACK_FONTS:
                if os.path.isfile(cand):
                    path = cand
                    print(f"[浮窗] 字体 '{family}' 未找到，回退到 {cand}")
                    break
            # 诊断：把用户级字体真实名字都列出来一次
            if HAS_PIL:
                scan = _scan_user_fonts()
                if scan:
                    names = sorted({n for n in scan})
                    print(f"[浮窗] 用户级字体真实名字（供排查）: {names}")
        _font_file_cache[family] = path
    return _font_file_cache[family]


def _get_pil_font(path, size_px):
    key = (path, size_px)
    if key not in _pil_font_cache:
        _pil_font_cache[key] = ImageFont.truetype(path, size_px)
    return _pil_font_cache[key]


class KaraokeOverlay:

    def __init__(self, cfg):
        self.cfg = cfg
        self._line = None
        self._next_line = ""
        self._progress = 0.0
        self._text_w = 0
        self._line_h = 0
        self._title = ""
        self._message = None

        self._text_photos = {"main": [], "sweep": []}

        self.root = tk.Tk()
        self.root.title("LMS Karaoke")
        self.root.geometry(f"{cfg.OVERLAY_W}x{cfg.OVERLAY_H}"
                           f"+{cfg.OVERLAY_X}+{cfg.OVERLAY_Y}")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=cfg.COLOR_KEY)
        try:
            self.root.attributes("-transparentcolor", cfg.COLOR_KEY)
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(self.root, bg=cfg.COLOR_KEY,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.sweep = tk.Canvas(self.root, bg=cfg.COLOR_KEY,
                               highlightthickness=0, bd=0)

        self._bind_drag(self.canvas)
        self._bind_drag(self.sweep)
        self._bind_menu(self.canvas)
        self._bind_menu(self.sweep)
        self.root.bind("<Configure>", lambda e: self._render())

    # ---------- 交互 ----------
    def _bind_drag(self, widget):
        d = {"x": 0, "y": 0}

        def on_press(e):
            d["x"], d["y"] = e.x, e.y

        def on_drag(e):
            self.root.geometry(f"+{self.root.winfo_x() + e.x - d['x']}"
                               f"+{self.root.winfo_y() + e.y - d['y']}")

        widget.bind("<Button-1>", on_press)
        widget.bind("<B1-Motion>", on_drag)

    def _bind_menu(self, widget):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="关闭歌词浮窗", command=self.hide)
        widget.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    # ---------- 对外接口 ----------
    def set_line(self, text, next_text="", title=""):
        self._line = text
        self._next_line = next_text
        self._title = title
        self._render()

    def set_progress(self, p):
        if self._line is None:
            return
        p = max(0.0, min(1.0, p))
        if abs(p - self._progress) < 0.002:
            return
        self._progress = p
        self._place_sweep()

    def clear(self, title="", message=None):
        self._line = None
        self._title = title
        self._message = message
        self.sweep.place_forget()
        self._render()

    # ---------- 渲染 ----------
    def _fit_font(self, text, max_w):
        size = self.cfg.FONT_SIZE
        while size > 12:
            f = tkfont.Font(family=self.cfg.FONT_FAMILY, size=size)
            if f.measure(text) <= max_w:
                return size, f
            size -= 1
        return size, tkfont.Font(family=self.cfg.FONT_FAMILY, size=size)

    def _draw_text(self, canvas, cx, cy, text, color, font):
        if text and HAS_PIL:
            if self._draw_text_pil(canvas, cx, cy, text, color, font):
                return
        stroke = getattr(self.cfg, "COLOR_STROKE", "#B8B8C4")
        for dx, dy in ((-1, -1), (-1, 0), (-1, 1),
                       (0, -1),           (0, 1),
                       (1, -1),  (1, 0),  (1, 1)):
            canvas.create_text(cx + dx, cy + dy, text=text,
                               font=font, fill=stroke)
        canvas.create_text(cx, cy, text=text, font=font, fill=color)

    def _draw_text_pil(self, canvas, cx, cy, text, color, font):
        if not text:
            return False

        try:
            size_pt = int(font.actual("size"))
        except tk.TclError:
            return False
        if size_pt <= 0:
            return False

        # ★ 用用户配置的族名优先，其次才用 tkinter 解析出的族名
        path = _find_font_file(self.cfg.FONT_FAMILY)
        if not path:
            try:
                path = _find_font_file(font.actual("family"))
            except tk.TclError:
                path = None
        if not path:
            print(f"[浮窗] 无法定位字体文件 "
                  f"family={self.cfg.FONT_FAMILY!r}")
            return False

        size_px = max(8, int(round(size_pt * 96 / 72)))
        try:
            pil_font = _get_pil_font(path, size_px)
        except Exception as e:
            print(f"[浮窗] 加载字体失败 {path}: {e}")
            return False

        sw = 1
        try:
            bbox = pil_font.getbbox(text, stroke_width=sw)
        except TypeError:
            bbox = pil_font.getbbox(text)

        pad = 6
        w = bbox[2] - bbox[0] + pad * 2
        h = bbox[3] - bbox[1] + pad * 2
        if w <= 0 or h <= 0:
            return False

        ox = pad - bbox[0]
        oy = pad - bbox[1]

        stroke = getattr(self.cfg, "COLOR_STROKE", "#B8B8C4")
        key_rgb = self._hex_to_rgb(self.cfg.COLOR_KEY)
        stroke_rgb = self._hex_to_rgb(stroke)
        color_rgb = self._hex_to_rgb(color)

        # ① 只画文字 → 拿文字形状的 alpha
        img_text = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(img_text).text((ox, oy), text, font=pil_font,
                                      fill=(255, 255, 255, 255))
        text_alpha = img_text.split()[3]

        # ② 画描边+文字 → 拿"含描边形状"的 alpha
        img_full = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        try:
            ImageDraw.Draw(img_full).text(
                (ox, oy), text, font=pil_font,
                fill=(255, 255, 255, 255),
                stroke_width=sw, stroke_fill=(255, 255, 255, 255))
        except TypeError:
            ImageDraw.Draw(img_full).text((ox, oy), text, font=pil_font,
                                          fill=(255, 255, 255, 255))
        full_alpha = img_full.split()[3]

        # ③ 阈值化：alpha≥128 视作命中 → 完全二值
        mask_full = full_alpha.point(lambda a: 255 if a >= 128 else 0)
        mask_text = text_alpha.point(lambda a: 255 if a >= 128 else 0)

        # ④ 合成：底=色键色 → 盖描边色 → 盖文字色
        result = Image.new("RGB", (w, h), key_rgb)
        result.paste(Image.new("RGB", (w, h), stroke_rgb), (0, 0), mask_full)
        result.paste(Image.new("RGB", (w, h), color_rgb), (0, 0), mask_text)

        try:
            photo = ImageTk.PhotoImage(result)
        except Exception as e:
            print(f"[浮窗] PhotoImage 失败: {e}")
            return False

        bucket = self._text_photos["main" if canvas is self.canvas else "sweep"]
        bucket.append(photo)
        canvas.create_image(cx, cy, image=photo, anchor="center")
        return True

    @staticmethod
    def _hex_to_rgb(h):
        h = (h or "#000000").lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        try:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except ValueError:
            return (0, 0, 0)

    def _render(self):
        cfg = self.cfg

        self._text_photos["main"].clear()
        self._text_photos["sweep"].clear()

        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10:
            return

        if self._line is None:
            self.sweep.place_forget()
            text = self._message or (f"♪ {self._title}" if self._title
                                     else "等待播放")
            f = tkfont.Font(family=cfg.FONT_FAMILY, size=cfg.FONT_SIZE - 8)
            self._draw_text(c, w // 2, h // 2, text, cfg.COLOR_IDLE, f)
            return

        size, f = self._fit_font(self._line, w - 80)
        self._text_w = f.measure(self._line)
        self._line_h = size + 18
        y_main = int(h * 0.42)

        self._draw_text(c, w // 2, y_main, self._line, cfg.COLOR_IDLE, f)

        if self._next_line:
            fn = tkfont.Font(family=cfg.FONT_FAMILY, size=max(12, size - 10))
            self._draw_text(c, w // 2, h - 18, self._next_line,
                            cfg.COLOR_IDLE, fn)

        sw = self.sweep
        sw.delete("all")
        sw.configure(height=self._line_h)
        self._draw_text(sw, self._text_w // 2, self._line_h // 2,
                        self._line, cfg.COLOR_ACTIVE, f)
        self._place_sweep()

    def _place_sweep(self):
        if self._line is None:
            self.sweep.place_forget()
            return
        c = self.canvas
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10:
            return
        y_main = int(h * 0.42)
        left = max(0, (w - self._text_w) // 2)
        sweep_px = max(1, int(self._progress * self._text_w))
        self.sweep.place(x=left, y=y_main - self._line_h // 2,
                         width=sweep_px, height=self._line_h)

    # ---------- 其它 ----------
    def get_position(self):
        try:
            return self.root.winfo_x(), self.root.winfo_y()
        except tk.TclError:
            return self.cfg.OVERLAY_X, self.cfg.OVERLAY_Y

    def show(self):
        self.root.deiconify()

    def hide(self):
        self.root.withdraw()

    def run(self):
        self.root.mainloop()