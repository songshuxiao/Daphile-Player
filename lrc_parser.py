# -*- coding: utf-8 -*-
"""LRC 解析：带时间轴歌词 → (时间, 文本) 序列"""

import re

_TS = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")

# 匹配元数据标签（如 LYRICIST=xxx, TITLE=xxx），连同后面的内容一并切除
_META_RE = re.compile(r'(LYRICIST|TITLE|ARTIST|ALBUM|BY|OFFSET|LENGTH|LYRICS)\s*=.*', re.IGNORECASE)


def _clean_text(text):
    """清理不可见控制字符（避免乱码），并剔除混在歌词里的元数据"""
    if not text:
        return ""
    # 1. 剔除乱码控制字符（如 \x00 等），只保留可打印字符和空白符
    text = "".join(ch for ch in text if ch.isprintable() or ch.isspace())
    # 2. 如果元数据和歌词在同一行，直接切除元数据及其后面的部分
    text = _META_RE.sub("", text)
    return text.strip()


class LrcParser:

    @staticmethod
    def parse(text):
        lines = []
        for raw in text.splitlines():
            stamps = []
            for m in _TS.finditer(raw):
                ms = int(m.group(3).ljust(3, "0")) / 1000.0 if m.group(3) else 0.0
                stamps.append(int(m.group(1)) * 60 + int(m.group(2)) + ms)
            
            if not stamps:
                continue
                
            content = _TS.sub("", raw).strip()
            content = _clean_text(content)
            
            # 清理后如果为空（例如该行只有元数据），则丢弃
            if not content:
                continue
                
            for t in stamps:
                lines.append((t, content))
                
        lines.sort(key=lambda x: x[0])
        return lines

    @staticmethod
    def find_current(lines, pos, offset=0.0):
        t = pos - (offset or 0.0)
        lo, hi, idx = 0, len(lines) - 1, -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if lines[mid][0] <= t:
                idx = mid
                lo = mid + 1
            else:
                hi = mid - 1
        # 同一时间戳有多行（原文 + 翻译）时，退回到第一行（原文）高亮
        while idx > 0 and lines[idx - 1][0] == lines[idx][0]:
            idx -= 1
        return idx

    @staticmethod
    def has_timestamps(text):
        return bool(text and _TS.search(text))

    @staticmethod
    def fake_lrc_from_plain(text, duration):
        rows = [r.strip() for r in text.splitlines() if r.strip()]
        if not rows:
            return ""
        duration = duration or len(rows) * 4.0
        step = max(duration / len(rows), 1.0)
        out = []
        for i, r in enumerate(rows):
            t = i * step
            out.append(f"[{int(t) // 60:02d}:{t % 60:05.2f}]{r}")
        return "\n".join(out)