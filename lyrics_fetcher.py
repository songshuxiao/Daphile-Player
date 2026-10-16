# -*- coding: utf-8 -*-
"""歌词多级获取 v2（并发竞速版）：
   ①内嵌(本地) ②内嵌(服务器下载) ③本地.lrc ④MAI插件 ⑤LRCLIB 五路并发；
   内嵌命中立即返回并取消其余；低优先级先命中则最多宽限 3 秒等内嵌。"""

import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from lms_client import LmsClient
from lrc_parser import LrcParser
from embedded_lyrics import EmbeddedLyrics

try:
    import requests
except ImportError:
    requests = None


class LyricsFetcher:

    def __init__(self, lms: LmsClient):
        self.lms = lms
        self._cache = {}
        self._dl_failed = set()

    # ---------- 主流程 ----------
    def fetch(self, track_id, artist, title, album, duration):
        if track_id in self._cache:
            return self._cache[track_id]

        local_path = self._url_to_local(self.lms.get_track_url(track_id))

        jobs = [
            (0, "内嵌歌词(本地)", self._t_local_embedded, (local_path,)),
            (0, "内嵌歌词(服务器)", self._t_download_embedded, (track_id,)),
            (1, "本地.lrc", self._t_sidecar, (local_path,)),
            (2, "MAI插件", self._t_mai,
             (track_id, artist, title, album, duration)),
            (4, "LRCLIB在线", self._t_lrclib, (artist, title, album)),
        ]

        best = None
        grace = 3.0
        final = None

        with ThreadPoolExecutor(max_workers=5, thread_name_prefix="lyrics") as ex:
            pending = set()
            for rank, src, fn, args in jobs:
                f = ex.submit(fn, *args)
                f._tag = (rank, src)
                pending.add(f)

            while pending:
                done, pending = wait(pending, timeout=grace,
                                     return_when=FIRST_COMPLETED)
                for f in done:
                    try:
                        r = f.result()
                    except Exception:
                        r = None
                    if not r:
                        continue
                    rank, src = f._tag
                    if rank == 0:                      # 内嵌命中 → 立即返回
                        for p in pending:
                            p.cancel()
                        final = (src, r)
                        break
                    if best is None or rank < best[0]:
                        best = (rank, src, r)
                if final:
                    break
                if not done and best:                  # 宽限耗尽，用最优命中
                    break

            if not final and best:
                final = (best[1], best[2])

        if final and final[1]:
            src, lrc = final
            if not LrcParser.has_timestamps(lrc):
                src = "纯文本(均分)"
                lrc = LrcParser.fake_lrc_from_plain(lrc, duration)
            final = (src, lrc)
        else:
            final = ("未找到", "")

        self._cache[track_id] = final
        print(f"[歌词] 来源: {final[0]}")
        return final

    # ---------- 各路任务 ----------
    def _t_local_embedded(self, path):
        if path and os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    raw = EmbeddedLyrics.extract(f.read())
                if raw:
                    print(f"[歌词] 内嵌命中(本地): {path}")
                    return raw
            except OSError:
                pass
        return None

    def _t_download_embedded(self, track_id):
        if not track_id or track_id in self._dl_failed:
            return None
        data = self.lms.download_track(track_id)
        if data:
            raw = EmbeddedLyrics.extract(data)
            if raw:
                print(f"[歌词] 内嵌命中(服务器下载, {len(data) // 1024}KB)")
                return raw
        self._dl_failed.add(track_id)
        return None

    def _t_sidecar(self, path):
        if not path:
            return None
        base, _ = os.path.splitext(path)
        for cand in (base + ".lrc", base + ".LRC"):
            try:
                with open(cand, "r", encoding="utf-8", errors="ignore") as f:
                    txt = f.read()
                if txt.strip():
                    return txt
            except OSError:
                continue
        return None

    def _t_mai(self, track_id, artist, title, album, duration):
        text, _err = self.lms.get_mai_lyrics(
            track_id, artist=artist, title=title, album=album,
            duration=duration, want_timestamps=True)
        return text or None

    def _t_lrclib(self, artist, title, album):
        if not (requests and title):
            return None
        try:
            r = requests.get("https://lrclib.net/api/search",
                             params={"track_name": title,
                                     "artist_name": artist or ""},
                             timeout=6,
                             headers={"Lrclib-App-Id": "lms-karaoke-py"})
            r.raise_for_status()
            best = None
            for it in r.json():
                if it.get("syncedLyrics"):
                    if album and it.get("albumName", "").lower() == album.lower():
                        return it["syncedLyrics"]
                    best = best or it["syncedLyrics"]
            return best
        except Exception:
            return None

    # ---------- 工具 ----------
    @staticmethod
    def _url_to_local(url):
        if not url or not url.lower().startswith("file:"):
            return None
        p = urllib.parse.unquote(urllib.parse.unquote(url))
        p = re.sub(r"^file://+", "", p, flags=re.I)
        p = p.replace("/", "\\") if re.match(r"^[A-Za-z]:\\", p) else p
        return os.path.normpath(p)
