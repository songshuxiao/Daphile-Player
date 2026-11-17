# -*- coding: utf-8 -*-
"""外部格式解码：用内置的 PyAV（自带 FFmpeg）解码 miniaudio 不支持的音频。

miniaudio 只认 WAV/FLAC/MP3/OGG；APE、M4A/AAC/ALAC、WMA、AIFF、DSF 等
交给 PyAV 解码成 44.1kHz / 16bit / 立体声 PCM，再交给播放引擎出声。

缺少 vendor/av 时 HAS_AV 为 False，上层会自动退回「让 ffmpeg 转码」或提示。
"""

import os
import sys

def _vendor_dir():
    """内置依赖目录：开发时是源码目录下的 vendor/，
       PyInstaller 打包后是解包目录 _MEIPASS/vendor（由 --add-data 提供）。"""
    cands = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        cands.append(os.path.join(meipass, "vendor"))
    cands.append(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "vendor"))
    for c in cands:
        if os.path.isdir(c):
            return c
    return ""


_VENDOR = _vendor_dir()
if _VENDOR and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

try:
    import av
    HAS_AV = True
except Exception as _e:                       # pragma: no cover - 环境相关
    av = None
    HAS_AV = False
    print(f"[本地播放] PyAV 不可用，APE/M4A/WMA 等格式无法本机解码: {_e}")

OUT_RATE = 44100
OUT_CHANNELS = 2


def decode_to_pcm(path, rate=OUT_RATE, channels=OUT_CHANNELS):
    """解码成 s16 交错 PCM，返回 (pcm_bytes, sample_rate, channels, duration)。"""
    if not HAS_AV:
        raise RuntimeError("PyAV 不可用")
    container = av.open(path)
    try:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="stereo",
                                      rate=rate)
        chunks = []

        def take(frames):
            for frame in frames or []:
                for plane in frame.planes:
                    chunks.append(bytes(plane))

        for frame in container.decode(stream):
            take(resampler.resample(frame))
        try:
            take(resampler.resample(None))     # 冲刷重采样缓冲
        except Exception:
            pass
        pcm = b"".join(chunks)
    finally:
        try:
            container.close()
        except Exception:
            pass
    frames = len(pcm) // max(1, 2 * channels)
    duration = frames / float(rate or OUT_RATE)
    return pcm, rate, channels, duration
