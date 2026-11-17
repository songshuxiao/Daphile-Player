# -*- coding: utf-8 -*-
"""本机音频播放引擎（Windows 扬声器输出）。

依赖内置在 vendor/ 里的 miniaudio（含 cffi），支持 MP3 / FLAC / WAV / OGG。
没有可用的音频设备或 miniaudio 加载失败时，HAS_AUDIO 为 False，上层功能自动降级。

设计：
  · 用 miniaudio.stream_file() 边解码边送声卡，不把整首歌读进内存；
  · 播放位置按单调时钟推算（声卡有 150ms 预缓冲，数帧会把进度算快）；
  · seek = 丢弃当前解码器、从目标帧重建，松手后立刻从新位置发声。
"""

import os
import sys
import threading
import time

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

# 本引擎能解码的扩展名（miniaudio：mp3 / flac / wav / ogg-vorbis）
AUDIO_EXTS = (".mp3", ".flac", ".wav", ".ogg")

try:
    import miniaudio
    HAS_AUDIO = True
except Exception as _e:                       # pragma: no cover - 环境相关
    miniaudio = None
    HAS_AUDIO = False
    print(f"[本地播放] miniaudio 不可用，Windows 扬声器功能将不可用: {_e}")

try:
    import numpy as _np
except Exception:                             # pragma: no cover - 环境相关
    _np = None


def sniff_ext(data):
    """按文件头猜扩展名。miniaudio 会按扩展名挑解码器，缓存文件必须带对后缀。"""
    if not data:
        return ".bin"
    head = bytes(data[:12])
    if head[:4] == b"fLaC":
        return ".flac"
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3",
                                          b"\xff\xf2", b"\xff\xfa"):
        return ".mp3"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    if head[:4] == b"OggS":
        return ".ogg"
    if head[4:8] == b"ftyp":
        return ".m4a"
    return ".mp3"


class LocalPlayer:
    """单文件播放器。线程安全：界面线程可随时调用 play/pause/seek/set_volume。"""

    def __init__(self, log=print):
        self._log = log
        self._lock = threading.RLock()
        self._device = None
        self._path = ""
        self._mode = "file"          # "file" = miniaudio 直接解码；"pcm" = 外部解码好的内存 PCM
        self._pcm = b""
        self._rate = 44100
        self._nch = 2
        self._duration = 0.0
        self._pos_sec = 0.0          # 暂停 / seek 时的权威位置
        self._play_t0 = None         # 本次播放开始时的单调时钟
        self._play_pos0 = 0.0        # 本次播放开始时的位置
        self._playing = False
        self._finished = False
        self._gain = 1.0
        self._volume = 100
        self._error = ""

    # ---------- 基本属性 ----------
    @property
    def available(self):
        return HAS_AUDIO

    @property
    def path(self):
        return self._path

    @property
    def duration(self):
        return self._duration

    @property
    def is_playing(self):
        return self._playing

    @property
    def finished(self):
        return self._finished

    @property
    def volume(self):
        return self._volume

    @property
    def error(self):
        return self._error

    def position(self):
        """当前播放位置（秒）。"""
        if self._playing and self._play_t0 is not None:
            pos = self._play_pos0 + (time.monotonic() - self._play_t0)
        else:
            pos = self._pos_sec
        if self._finished:
            pos = self._duration or pos
        if self._duration:
            pos = min(pos, self._duration)
        return max(0.0, pos)

    # ---------- 装载 ----------
    def load(self, path):
        """装载一个音频文件；返回时长（秒）。失败抛异常。"""
        if not HAS_AUDIO:
            raise RuntimeError("本机音频引擎不可用（缺少 miniaudio）")
        path = os.path.abspath(str(path))
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        info = miniaudio.get_file_info(path)
        with self._lock:
            self._playing = False
            self._play_t0 = None
            self._stop_device()
            self._mode = "file"
            self._pcm = b""
            self._path = path
            self._rate = int(info.sample_rate or 44100)
            self._nch = 2
            self._duration = float(info.duration or 0.0)
            self._pos_sec = 0.0
            self._finished = False
            self._error = ""
        return self._duration

    def load_pcm(self, pcm, sample_rate=44100, channels=2, duration=0.0,
                 path="(memory)"):
        """装载外部解码好的 s16 交错 PCM（用于 APE 等 miniaudio 解不了的格式）。"""
        if not HAS_AUDIO:
            raise RuntimeError("本机音频引擎不可用（缺少 miniaudio）")
        with self._lock:
            self._playing = False
            self._play_t0 = None
            self._stop_device()
            self._mode = "pcm"
            self._pcm = bytes(pcm or b"")
            self._path = path
            self._rate = int(sample_rate or 44100)
            self._nch = int(channels or 2)
            self._duration = float(
                duration or (len(self._pcm) / float(2 * self._nch * self._rate)))
            self._pos_sec = 0.0
            self._finished = False
            self._error = ""
        return self._duration

    def unload(self):
        with self._lock:
            self._playing = False
            self._play_t0 = None
            self._stop_device()
            self._path = ""
            self._mode = "file"
            self._pcm = b""
            self._duration = 0.0
            self._pos_sec = 0.0
            self._finished = False

    # ---------- 播放控制 ----------
    def play(self):
        with self._lock:
            if not self._path:
                return False
            if self._playing:
                return True
            if self._finished or (self._duration
                                  and self._pos_sec >= self._duration - 0.05):
                self._pos_sec = 0.0
            self._finished = False
            # ★ 必须先收掉上一台设备：曲尾 EOF 后设备不会自己停，
            #   直接再建一台会让旧设备的音频线程一直活着（进程退不出去）
            self._stop_device()
            try:
                self._start_device(self._pos_sec)
            except Exception as e:
                self._error = f"{type(e).__name__}: {e}"
                self._log(f"[本地播放] 启动音频设备失败: {self._error}")
                self._playing = False
                return False
            self._play_pos0 = self._pos_sec
            self._play_t0 = time.monotonic()
            self._playing = True
            self._error = ""
            return True

    def pause(self):
        with self._lock:
            if self._playing:
                self._pos_sec = self.position()
            self._playing = False
            self._play_t0 = None
            self._stop_device()

    def toggle(self):
        if self._playing:
            self.pause()
            return False
        self.play()
        return self._playing

    def stop(self):
        with self._lock:
            self._playing = False
            self._play_t0 = None
            self._stop_device()
            self._pos_sec = 0.0
            self._finished = False

    def seek(self, sec):
        """跳到指定秒。播放中会重建解码器，松手后立刻从新位置发声。"""
        try:
            sec = float(sec)
        except (TypeError, ValueError):
            return
        with self._lock:
            was_playing = self._playing
            if self._duration:
                sec = max(0.0, min(sec, max(0.0, self._duration - 0.05)))
            else:
                sec = max(0.0, sec)
            if was_playing:
                self._pos_sec = self.position()
            self._playing = False
            self._play_t0 = None
            self._stop_device()
            self._pos_sec = sec
            self._finished = False
            if was_playing:
                self.play()

    def set_volume(self, pct):
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            return
        pct = max(0.0, min(100.0, pct))
        with self._lock:
            self._volume = int(pct)
            # 感知近似：平方曲线，小音量下听感更接近
            self._gain = (pct / 100.0) ** 1.6

    def close(self):
        with self._lock:
            self._playing = False
            self._play_t0 = None
            self._stop_device()
            self._path = ""

    # ---------- 设备 ----------
    def _start_device(self, start_sec):
        fmt = miniaudio.SampleFormat.SIGNED16
        if self._mode == "pcm":
            gen = self._pcm_gen(start_sec)
        else:
            frame = int(max(0.0, start_sec) * self._rate)
            gen = miniaudio.stream_file(
                self._path, output_format=fmt, nchannels=self._nch,
                sample_rate=self._rate, frames_to_read=2048,
                seek_frame=frame)
        next(gen)                      # 预热：之后才能 send(frames)
        feeder = self._feeder(gen)
        next(feeder)                   # 预热
        dev = miniaudio.PlaybackDevice(
            output_format=fmt, nchannels=self._nch,
            sample_rate=self._rate, buffersize_msec=150,
            app_name="Daphile-Player")
        dev.start(feeder)
        self._device = dev

    def _pcm_gen(self, start_sec):
        """从内存 PCM 里按帧切片（外部解码好的数据）。"""
        frame_bytes = 2 * self._nch
        offset = int(max(0.0, start_sec) * self._rate) * frame_bytes
        total = len(self._pcm)
        n = yield
        while offset < total:
            want = int(n or 2048) * frame_bytes
            chunk = self._pcm[offset:offset + want]
            offset += len(chunk)
            gain = self._gain
            if gain != 1.0 and chunk:
                chunk = self._apply_gain(chunk, gain)
            n = yield chunk

    def _stop_device(self):
        dev = self._device
        self._device = None
        if dev is None:
            return
        try:
            dev.stop()
        except Exception:
            pass
        try:
            dev.close()
        except Exception:
            pass

    def _feeder(self, gen):
        """把解码器输出按声卡要的帧数喂给设备；曲尾时标记结束。"""
        frames = yield                 # 预热占位
        while True:
            try:
                chunk = gen.send(frames)
            except StopIteration:
                self._playing = False
                self._finished = True
                return
            except Exception as e:     # 解码错误：停下来，别把异常抛回声卡回调
                self._error = f"{type(e).__name__}: {e}"
                self._log(f"[本地播放] 解码失败: {self._error}")
                self._playing = False
                return
            try:
                raw = chunk.tobytes() if hasattr(chunk, "tobytes") else bytes(chunk)
            except Exception:
                raw = b""
            gain = self._gain
            if gain != 1.0 and raw:
                raw = self._apply_gain(raw, gain)
            frames = yield raw

    @staticmethod
    def _apply_gain(chunk, gain):
        if _np is None:
            return chunk
        try:
            arr = _np.frombuffer(chunk, dtype=_np.int16).astype(_np.float32)
            arr *= gain
            return _np.clip(arr, -32768, 32767).astype(_np.int16).tobytes()
        except Exception:
            return chunk
