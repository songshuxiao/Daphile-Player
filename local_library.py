# -*- coding: utf-8 -*-
"""本地音乐库：扫描指定文件夹，按歌手 / 专辑归档，供「歌手」页与「Windows 扬声器」播放。

· 标签读取用 mutagen（项目依赖，已在 requirements.txt 中）；
  读不到标签时退回文件名 / 父文件夹名，保证「能扫到、能显示、能播」。
· 索引结果缓存到 local-library.json，避免每次启动重扫整个音乐目录。
"""

import hashlib
import json
import os
import threading

try:
    from mutagen import File as _MFile
    from mutagen.id3 import ID3
    HAS_MUTAGEN = True
except Exception:                       # pragma: no cover - 环境相关
    _MFile = None
    HAS_MUTAGEN = False
    print("[本地音乐] mutagen 不可用，只能按文件名猜测曲目信息")

# 能被本机引擎播放的格式（miniaudio 支持）
PLAYABLE_EXTS = (".mp3", ".flac", ".wav", ".ogg")

# 扫描时认作音乐文件的扩展名（不支持的格式也会显示，但点了会提示无法播放）
AUDIO_EXTS = (".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".wma",
              ".ape", ".aiff", ".aif", ".dsf", ".dff", ".opus", ".wv")

_LOCAL_PREFIX = "L|"


def is_local_id(tid):
    return str(tid or "").startswith(_LOCAL_PREFIX)


def local_track_id(path):
    key = os.path.normcase(os.path.abspath(path)).encode("utf-8", "ignore")
    return _LOCAL_PREFIX + hashlib.sha1(key).hexdigest()[:16]


def name_key(name):
    return str(name or "").strip().lower()


def _txt(v):
    if v is None:
        return ""
    text = getattr(v, "text", None)      # ID3 帧对象
    if text is not None:
        v = text
    if isinstance(v, (list, tuple)):
        return str(v[0]).strip() if v else ""
    return str(v).strip()


def _has_cjk(s):
    return any("\u3400" <= ch <= "\u9fff" for ch in s or "")


def fix_text(s):
    """修常见标签乱码：GBK / UTF-8 字节被当成 latin-1 读进来。

    判断逻辑：
      · 已经是正常中文 → 原样返回；
      · 先试 latin-1 → UTF-8（UTF-8 标签被误读的最常见情形）；
      · 再试 latin-1 → GB18030（国内 GBK 标签被误读）；
      · 只有结果里出现汉字才采用，避免误伤正常的西文标签。"""
    if not s or _has_cjk(s):
        return s
    for enc in ("utf-8", "gb18030"):
        try:
            cand = s.encode("latin-1").decode(enc)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if cand and cand != s and _has_cjk(cand):
            return cand
    return s


class LocalLibrary:

    VERSION = 1

    def __init__(self, cache_path):
        self.cache_path = cache_path
        self.folders = []
        self.scanned_at = 0
        self.tracks = {}          # tid -> dict
        self._artists = {}        # name_key -> {"name", "albums": {album_key: {...}}}
        self._lock = threading.RLock()

    # ---------- 持久化 ----------
    def load(self):
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict):
            return False
        self.folders = [str(x) for x in (data.get("folders") or []) if x]
        self.scanned_at = data.get("scanned_at") or 0
        tracks = [t for t in (data.get("tracks") or []) if isinstance(t, dict)
                  and t.get("id") and t.get("path")]
        self._rebuild(tracks)
        print(f"[本地音乐] 已载入索引缓存: {len(self.tracks)} 首")
        return True

    def save(self):
        data = {
            "version": self.VERSION,
            "folders": list(self.folders),
            "scanned_at": self.scanned_at,
            "tracks": sorted(self.tracks.values(),
                             key=lambda t: (t.get("artist", ""),
                                            t.get("album", ""),
                                            t.get("disc", 1),
                                            t.get("track", 0),
                                            t.get("title", ""))),
        }
        try:
            os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            return True
        except OSError as e:
            print(f"[本地音乐] 保存索引失败: {e}")
            return False

    def set_folders(self, folders):
        self.folders = [str(x) for x in (folders or []) if x]

    # ---------- 扫描 ----------
    def scan(self, progress=None):
        """重新扫描所有文件夹。progress(done, total, path) 可选，用于显示进度。
           返回曲目数量。"""
        found = []
        pairs = []          # (path, 扫描根目录)
        for root_dir in self.folders:
            if not os.path.isdir(root_dir):
                continue
            for root, dirs, files in os.walk(root_dir):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fn in files:
                    if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                        pairs.append((os.path.join(root, fn), root_dir))

        total = len(pairs)
        for i, (path, root_dir) in enumerate(pairs):
            if progress:
                try:
                    progress(i + 1, total, path)
                except Exception:
                    pass
            info = self.read_tags(path, root=root_dir)
            if info:
                found.append(info)

        with self._lock:
            self._rebuild(found)
            import time
            self.scanned_at = int(time.time())
        self.save()
        print(f"[本地音乐] 扫描完成: {len(found)} 首 / {total} 个文件")
        return len(found)

    def read_tags(self, path, root=None):
        """读一个文件的曲目信息；读不到标签时按目录/文件名兜底。

           ★ 歌手只从「相对扫描根目录」的第一层目录推断，
             绝不把根目录名（如 Music）当成歌手；
           ★ 专辑没有标签时用歌曲名（按需求），不再用文件夹名。"""
        ext = os.path.splitext(path)[1].lower()
        title = os.path.splitext(os.path.basename(path))[0]
        artist = album = ""
        trackno = disc = 0
        year = ""
        duration = 0.0

        info = self._read_mutagen(path)
        if info:
            title = info.get("title") or title
            artist = info.get("artist") or ""
            album = info.get("album") or ""
            trackno = self._to_int(info.get("track"))
            disc = self._to_int(info.get("disc"))
            year = (info.get("year") or "")[:4]
            duration = info.get("duration") or 0.0

        if not artist:
            artist = self._artist_from_path(path, root)
        if not album:
            # 没有专辑标签 → 用歌曲名当专辑名
            album = title or "未知专辑"

        return {
            "id": local_track_id(path),
            "path": os.path.abspath(path),
            "title": title or os.path.basename(path),
            "artist": artist or "未知歌手",
            "album": album or "未知专辑",
            "track": trackno,
            "disc": disc,
            "year": year,
            "duration": round(duration, 3),
            "ext": ext.lstrip("."),
            "playable": ext in PLAYABLE_EXTS,
        }

    # 「库根目录」常见名字：用户选中的是音乐总目录，而不是某个歌手
    _GENERIC_ROOTS = {"music", "musics", "music library", "my music", "mymusic",
                      "音乐", "我的音乐", "歌曲", "音乐库", "audio", "音频",
                      "media", "media library"}
    # 这些名字更像「专辑/临时目录」，其父目录才是歌手
    _ALBUMISH = {"new", "新建文件夹", "未分类", "unknown", "unknown album",
                 "未命名", "未命名文件夹", "cd1", "cd2"}

    @classmethod
    def _artist_from_path(cls, path, root=None):
        """从目录结构猜歌手（仅在标签里没有歌手时使用）。

           兼容两种常见选法：
             · 选中「音乐总目录」：D:\\Music\\歌手\\专辑\\x.mp3  → 取第一层子目录
             · 选中「歌手目录」：  C:\\...\\Music\\周杰伦\\x.mp3  → 取选中目录本身
               （绝不会退到上一级的 Music，那正是之前冒出「Music」的原因）"""
        if not root:
            name = os.path.basename(os.path.dirname(path)).strip()
            return name or "未知歌手"

        root = os.path.normpath(root)
        root_name = os.path.basename(root).strip()
        low = root_name.lower()

        try:
            rel = os.path.relpath(path, root)
            dirs = [p for p in os.path.dirname(rel).split(os.sep)
                    if p and p != "."]
        except (ValueError, OSError):
            dirs = []

        if low in cls._ALBUMISH:
            parent = os.path.basename(os.path.dirname(root)).strip()
            return parent or root_name or "未知歌手"

        if len(dirs) >= 2:
            return dirs[0]
        if len(dirs) == 1:
            if low in cls._GENERIC_ROOTS:
                return dirs[0]
            return root_name or "未知歌手"
        if low in cls._GENERIC_ROOTS:
            return "未知歌手"
        return root_name or "未知歌手"

    @staticmethod
    def _read_mutagen(path):
        """读标签，兼容 ID3(mp3/wav/aiff) / Vorbis(flac/ogg) / MP4 三种键名。"""
        if not HAS_MUTAGEN or _MFile is None:
            return {}
        try:
            f = _MFile(path)
        except Exception as e:
            print(f"[本地音乐] 读标签失败 {path}: {type(e).__name__}")
            return {}
        if f is None:
            return {}
        out = {}
        try:
            out["duration"] = float(getattr(f.info, "length", 0) or 0)
        except Exception:
            out["duration"] = 0.0

        tags = getattr(f, "tags", None)
        if tags is None:
            return out

        def grab(*keys):
            for k in keys:
                v = None
                try:
                    v = tags.get(k)
                except Exception:
                    v = None
                if v is None:
                    try:
                        v = getattr(tags, k)
                    except Exception:
                        v = None
                if v:
                    s = _txt(v)
                    if s:
                        return s
            return ""

        out["title"] = fix_text(grab("title", "TIT2", "\xa9nam"))
        out["artist"] = fix_text(grab("artist", "TPE1", "albumartist", "TPE2",
                                      "ARTIST", "ALBUMARTIST", "\xa9ART", "aART"))
        out["album"] = fix_text(grab("album", "TALB", "ALBUM", "\xa9alb"))
        out["track"] = grab("tracknumber", "TRCK", "track", "TRACKNUMBER", "trkn")
        out["disc"] = grab("discnumber", "TPOS", "disc", "DISCNUMBER", "disk")
        out["year"] = grab("date", "TDRC", "DATE", "year", "YEAR", "\xa9day")
        return out

    @staticmethod
    def _to_int(s):
        try:
            digits = ""
            for ch in str(s):
                if ch.isdigit():
                    digits += ch
                elif digits:
                    break
            return int(digits) if digits else 0
        except Exception:
            return 0

    # ---------- 索引 ----------
    def _rebuild(self, tracks):
        self.tracks = {}
        self._artists = {}
        for t in tracks:
            tid = t.get("id")
            if not tid:
                continue
            self.tracks[tid] = t
            akey = name_key(t.get("artist"))
            alb_key = name_key(t.get("album"))
            node = self._artists.setdefault(
                akey, {"name": t.get("artist") or "未知歌手", "albums": {}})
            alb = node["albums"].setdefault(
                alb_key, {"name": t.get("album") or "未知专辑", "tracks": []})
            alb["tracks"].append(tid)
        for node in self._artists.values():
            for alb in node["albums"].values():
                alb["tracks"].sort(key=self._sort_key)

    def _sort_key(self, tid):
        t = self.tracks.get(tid) or {}
        return (t.get("disc") or 1, t.get("track") or 0,
                str(t.get("title") or "").lower())

    def count(self):
        return len(self.tracks)

    def artists(self):
        """[(显示名, 曲目数)]，按名字排序。"""
        out = []
        for node in self._artists.values():
            n = sum(len(a["tracks"]) for a in node["albums"].values())
            out.append((node["name"], n))
        out.sort(key=lambda x: x[0].lower())
        return out

    def has_artist(self, name):
        return name_key(name) in self._artists

    def albums(self, artist_name):
        """[(专辑名, 曲目数)]"""
        node = self._artists.get(name_key(artist_name))
        if not node:
            return []
        out = [(a["name"], len(a["tracks"])) for a in node["albums"].values()]
        out.sort(key=lambda x: x[0].lower())
        return out

    def tracks_of(self, artist_name, album_name):
        """该歌手该专辑下的曲目 dict 列表（按碟号/音轨号排序）。"""
        node = self._artists.get(name_key(artist_name))
        if not node:
            return []
        alb = node["albums"].get(name_key(album_name))
        if not alb:
            return []
        return [self.tracks[t] for t in alb["tracks"] if t in self.tracks]

    def get(self, tid):
        return self.tracks.get(str(tid))

    def folder_of(self, tid):
        t = self.get(tid)
        return os.path.dirname(t["path"]) if t else ""

    # ---------- 附加信息 ----------
    def read_cover(self, tid):
        """取内嵌封面字节（APIC / covr），没有就返回 None。"""
        t = self.get(tid)
        if not t or not HAS_MUTAGEN:
            return None
        try:
            f = _MFile(t["path"])
            if f is None:
                return None
            tags = getattr(f, "tags", None)
            if tags is None:
                return None
            # ID3 (mp3 / wav / aiff)
            for key in list(tags.keys()):
                if str(key).startswith("APIC"):
                    return bytes(tags[key].data)
            # FLAC / ogg
            pics = getattr(f, "pictures", None)
            if pics:
                return bytes(pics[0].data)
            # mp4
            covr = tags.get("covr")
            if covr:
                return bytes(covr[0])
        except Exception:
            pass
        return None

    def read_lyrics(self, tid):
        """取内嵌歌词或同名 .lrc 文件内容。"""
        t = self.get(tid)
        if not t:
            return ""
        # ① 同名 .lrc
        base = os.path.splitext(t["path"])[0]
        for ext in (".lrc", ".LRC"):
            p = base + ext
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        return f.read()
                except OSError:
                    pass
        # ② 内嵌歌词
        if not HAS_MUTAGEN:
            return ""
        try:
            f = _MFile(t["path"])
            tags = getattr(f, "tags", None)
            if tags is None:
                return ""
            for key in list(tags.keys()):
                k = str(key)
                if k.startswith("USLT") or k in ("LYRICS", "UNSYNCEDLYRICS"):
                    val = tags[key]
                    return str(getattr(val, "text", val) or "")
        except Exception:
            pass
        return ""
