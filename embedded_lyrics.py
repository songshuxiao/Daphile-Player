# -*- coding: utf-8 -*-
"""从音轨原始数据（ID3v2 USLT / FLAC LYRICS 块等）提取内嵌 LRC 文本"""

import re

_TS = re.compile(r"^\s*\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]")


class EmbeddedLyrics:

    @staticmethod
    def extract(data: bytes):
        if not data:
            return None
        for enc in ("utf-8", "gb18030", "latin-1"):
            try:
                text = data.decode(enc, errors="ignore")
            except Exception:
                continue
            best, cur = [], []
            for ln in text.splitlines():
                if _TS.match(ln):
                    cur.append(ln)
                else:
                    if len(cur) > len(best):
                        best = cur
                    cur = []
            if len(cur) > len(best):
                best = cur
            if len(best) >= 3:
                return "\n".join(best)
        return None
