# -*- coding: utf-8 -*-
"""LMS/Daphile JSON-RPC 客户端
   · ★ 切歌最简方案：一次 status 拉回整个当前队列（含 album_id/artist_id），
     内存中直接算上一曲/下一曲位置，一条 playlist index 跳过去。
     不依赖 songinfo，不重载队列。
   · 优先级：同专辑 → 同歌手 → 队列顺序
   · 线程锁串行化 + 自动重试"""

import json
import time
import socket
import threading
import urllib.request
import urllib.parse

from lrc_parser import LrcParser


class LmsClient:

    def __init__(self, host, port, player_mac=""):
        self.host = host
        self.port = port
        self._mac = player_mac or ""
        self.server_name = ""
        self._lock = threading.Lock()
    # ---------- Wake-on-LAN ----------
    @staticmethod
    def send_wol(mac, ip="255.255.255.255", port=9):
        """发送 WOL 魔术包
           mac : 目标主机网卡 MAC，支持 AA:BB:CC:DD:EE:FF / AA-BB-... / AABBCCDDEEFF
           ip  : 广播地址，建议子网定向广播（如 192.168.3.255）
           port: 9 或 7"""
        try:
            mac_clean = (mac.replace(":", "").replace("-", "")
                         .replace(".", "").replace(" ", ""))
            if len(mac_clean) != 12:
                raise ValueError(f"MAC 长度错误: {mac!r}")
            mac_bytes = bytes.fromhex(mac_clean)
            packet = b"\xff" * 6 + mac_bytes * 16
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(packet, (ip, port))
            sock.close()
            print(f"[WOL] 魔术包已发送: {ip}:{port} (MAC={mac})")
            return True
        except Exception as e:
            print(f"[WOL] 发送失败: {e}")
            return False
    # ---------- 底层 HTTP ----------
    def _post(self, player, cmd, retries=3):
        url = f"http://{self.host}:{self.port}/jsonrpc.js"
        payload = {"id": 1, "method": "slim.request",
                   "params": [player, cmd]}
        body = json.dumps(payload).encode("utf-8")
        last_err = None
        for attempt in range(retries):
            try:
                with self._lock:
                    req = urllib.request.Request(
                        url, data=body,
                        headers={"Content-Type": "application/json",
                                 "Connection": "close"})
                    with urllib.request.urlopen(req, timeout=8) as r:
                        return json.loads(r.read().decode("utf-8")).get("result", {})
            except (urllib.error.URLError, ConnectionError, OSError) as e:
                last_err = e
                if attempt < retries - 1:
                    time.sleep(0.3 * (attempt + 1))
        raise last_err

    # ---------- MAC 解析（★ 必须指向真实播放器） ----------
    def player_mac(self):
        if self._mac:
            return self._mac
        try:
            res = self._post("-", ["players", 0, 99])
            players = res.get("players_loop", [])
            if players:
                self._mac = players[0].get("playerid",
                                           players[0].get("id", ""))
                print(f"[LMS] 已解析播放器 MAC: {self._mac}")
        except Exception as e:
            print(f"[LMS] 播放器 MAC 解析失败: {e}")
        return self._mac

    def set_player_mac(self, mac):
        """切换目标播放器 MAC"""
        self._mac = mac or ""
        print(f"[LMS] 切换播放器至: {self._mac}")

    def command(self, cmd, player=None, retries=3):
        target = player if player is not None else (self.player_mac() or "-")
        return self._post(target, cmd, retries)

    def list_players(self):
        res = self._post("-", ["players", 0, 99])
        return [(p.get("playerid", p.get("id", "")), p.get("name", ""))
                for p in res.get("players_loop", [])]

    # ---------- 状态（★ 一次拉全队列，含 album_id/artist_id） ----------
    def get_status(self):
        # 从队列头开始取，确保拿到完整队列
        res = self.command(["status", 0, 500, "tags:dglsyeatK"])
        loop = res.get("playlist_loop", []) or []

        try:
            vol = float(res.get("mixer volume", 50) or 50)
        except (TypeError, ValueError):
            vol = 50.0

        try:
            cur_index = int(res.get("playlist_cur_index", -1))
        except (TypeError, ValueError):
            cur_index = -1
        if cur_index < 0 or cur_index >= len(loop):
            cur_index = -1

        queue = []
        for i, t in enumerate(loop):
            queue.append({
                "pos": i,
                "id": str(t.get("id", "")),
                "title": t.get("title", ""),
                "album_id": t.get("album_id"),
                "artist_id": t.get("artist_id"),
                "tracknum": t.get("tracknum", 0),
            })

        # 兜底：用 track_id 匹配当前曲目
        if cur_index < 0 and loop:
            cur_id = str(loop[0].get("id", ""))
            cur_index = next((q["pos"] for q in queue if q["id"] == cur_id), -1)
        if cur_index < 0 and queue:
            cur_index = 0

        cur_raw = loop[cur_index] if 0 <= cur_index < len(loop) else {}

        return {
            "mode": res.get("mode", ""),
            "time": float(res.get("time", 0) or 0),
            "duration": float(cur_raw.get("duration", 0) or 0),
            "track_id": cur_raw.get("id"),
            "title": cur_raw.get("title", ""),
            "artist": cur_raw.get("artist", ""),
            "album": cur_raw.get("album", ""),
            "coverid": str(cur_raw.get("coverid", "") or ""),
            "volume": vol,
            "queue": queue,
            "cur_index": cur_index,
        }

    # ---------- 播放控制 ----------
    def play(self):
        self.command(["play"])

    def pause(self):
        self.command(["pause"])

    def stop(self):
        self.command(["stop"])

    def set_volume(self, val):
        self.command(["mixer", "volume", str(int(val))])

    def seek(self, sec):
        self.command(["time", str(int(sec))])

    def next_track(self):
        self._jump(+1)

    def prev_track(self):
        self._jump(-1)

    # ---------- 上/下一曲（★ 纯内存计算 + 一条 index 命令） ----------
    def _jump(self, step):
        dir_txt = "下一曲" if step > 0 else "上一曲"
        try:
            st = self.get_status()
            queue = st.get("queue", [])
            cur_pos = st.get("cur_index", -1)

            if not queue or cur_pos < 0:
                print(f"[切歌] {dir_txt}: 队列为空或无当前索引，button 兜底")
                self._button_step(step)
                return

            cur = next((q for q in queue if q["pos"] == cur_pos), None)
            if cur is None:
                print(f"[切歌] {dir_txt}: 队列中未找到当前曲，button 兜底")
                self._button_step(step)
                return

            # ① 同专辑（按 tracknum 排序，循环）
            same_album = [q for q in queue
                          if cur["album_id"]
                          and q["album_id"] == cur["album_id"]]
            if len(same_album) >= 2:
                same_album.sort(
                    key=lambda q: (int(q.get("tracknum") or 0), q["pos"]))
                i = next((k for k, q in enumerate(same_album)
                          if q["id"] == cur["id"]), -1)
                if i >= 0:
                    j = (i + step) % len(same_album)
                    self._goto(same_album[j],
                               f"同专辑({len(same_album)}首)")
                    return

            # ② 同歌手
            same_artist = [q for q in queue
                           if cur["artist_id"]
                           and q["artist_id"] == cur["artist_id"]]
            if len(same_artist) >= 2:
                same_artist.sort(key=lambda q: q["pos"])
                i = next((k for k, q in enumerate(same_artist)
                          if q["id"] == cur["id"]), -1)
                if i >= 0:
                    j = (i + step) % len(same_artist)
                    self._goto(same_artist[j],
                               f"同歌手({len(same_artist)}首)")
                    return

            # ③ 队列顺序（循环）
            i = next((k for k, q in enumerate(queue)
                      if q["id"] == cur["id"]), -1)
            if i >= 0:
                j = (i + step) % len(queue)
                self._goto(queue[j], "队列顺序")
                return

            self._button_step(step)

        except Exception as e:
            print(f"[切歌] {dir_txt} 异常: {e}，button 兜底")
            try:
                self._button_step(step)
            except Exception as e2:
                print(f"[切歌] button 兜底也失败: {e2}")

    def _goto(self, target, ctx_name):
        """跳到队列中的目标位置并播放"""
        self.command(["playlist", "index", str(target["pos"])])
        print(f"[切歌] ✓ {ctx_name} → 第 {target['pos'] + 1} 首: "
              f"{target.get('title', '')}")

    def _button_step(self, step):
        """最终兜底：button 命令"""
        self.command(["button", "jump_fwd" if step > 0 else "jump_rew"])
        print("[切歌] ✓ button 兜底完成")

    # ---------- 音乐库浏览 ----------
    def browse_artists(self):
        res = self.command(["artists", 0, 500], player="-")
        return [(a.get("id"), a.get("artist") or a.get("name", ""))
                for a in res.get("artists_loop", [])]

    def browse_albums(self, artist_id):
        res = self.command(["albums", 0, 500, f"artist_id:{artist_id}"],
                           player="-")
        return [(a.get("id"), a.get("album") or a.get("title", ""))
                for a in res.get("albums_loop", [])]

    def browse_tracks(self, album_id):
        res = self.command(["titles", 0, 500, f"album_id:{album_id}"],
                           player="-")
        return [(t.get("id"), t.get("tracknum", 0), t.get("title", ""))
                for t in res.get("titles_loop", [])]

    def search(self, query):
        """搜索：返回 {tracks, artists, albums} 字典。
           · tracks: [(id, tracknum, title, artist, album, album_id), ...] 6 元组
           · artists: [(id, name), ...]
           · albums:  [(id, name), ...]
           · 请求 tags:aA l t e —— artist / albumartist / album / tracknum / album_id
             album_id 是歌曲的必带字段，用于点击专辑时精确加载"""
        q = f"search:{query}"
        ql = (query or "").strip().lower()

        def _hit(s):
            if not ql:
                return True
            return ql in (s or "").lower()

        tracks, artists, albums = [], [], []

        # 歌曲：a=artist A=albumartist l=album t=tracknum e=album_id
        try:
            res = self.command(["titles", 0, 200, "tags:aAlte", q], player="-")
            for t in res.get("titles_loop", []) or []:
                tid = t.get("id")
                tno = t.get("tracknum", 0) or 0
                tname = t.get("title", "") or ""
                artist = t.get("artist", "") or ""
                albumartist = t.get("albumartist", "") or ""
                album = t.get("album", "") or ""
                album_id = str(t.get("album_id")
                               or t.get("albumId") or "")
                # ★ artist 为空时用 albumartist 兜底，确保歌曲行能显示歌手
                if not artist and albumartist:
                    artist = albumartist
                if _hit(tname) or _hit(artist) or _hit(album):
                    tracks.append((tid, tno, tname, artist, album, album_id))
        except Exception as e:
            print(f"[搜索] tracks 失败: {e}")

        # 艺人
        try:
            res = self.command(["artists", 0, 100, "tags:s", q], player="-")
            for a in res.get("artists_loop", []) or []:
                aid = a.get("id")
                aname = a.get("artist") or a.get("name", "") or ""
                if _hit(aname):
                    artists.append((aid, aname))
        except Exception as e:
            print(f"[搜索] artists 失败: {e}")

        # 专辑
        try:
            res = self.command(["albums", 0, 100, "tags:al", q], player="-")
            for al in res.get("albums_loop", []) or []:
                alid = al.get("id")
                alname = al.get("album") or al.get("title", "") or ""
                if _hit(alname):
                    albums.append((alid, alname))
        except Exception as e:
            print(f"[搜索] albums 失败: {e}")

        return {"artists": artists, "albums": albums, "tracks": tracks}

    # ---------- 播放库中歌曲 ----------
    def play_track(self, track_id):
        """播放库中歌曲：
           ① 加载整张专辑到队列
           ② 从 status 读回真实队列，按 track_id 精确匹配 playlist index
           ③ 用 playlist index 命令跳到目标曲目
           （★ 修复：旧代码用 titles 返回的 tracknum 序号当 index 用，
             但 loadalbum 加载顺序和 titles 排序可能不一致，会跳到错误的歌）"""
        track_id = str(track_id)

        # 1. 获取专辑名 / 艺人名
        album_name = ""
        artist_name = ""
        try:
            res = self.command(["songinfo", 0, 10, f"track_id:{track_id}",
                                "tags:al"])
            for item in res.get("songinfo_loop", []) or []:
                if "album" in item and not album_name:
                    album_name = item["album"]
                if "artist" in item and not artist_name:
                    artist_name = item["artist"]
        except Exception as e:
            print(f"[播放] 获取专辑信息失败: {e}")

        if not album_name:
            print("[播放] 无法获取专辑名，回退到单曲加载")
            self._play_single(track_id)
            return

        # 2. 加载整张专辑
        try:
            res = self.command(["playlist", "loadalbum", "",
                                artist_name, album_name])
            print(f"[播放] loadalbum '{artist_name}' - '{album_name}' → {res}")
        except Exception as e:
            print(f"[播放] loadalbum 失败: {e}")
            self._play_single(track_id)
            return

        # 3. 等 LMS 完成队列构建
        time.sleep(0.6)

        # 4. 从 status 读回真实队列，按 track_id 精确匹配位置
        target_index = -1
        try:
            st = self.get_status()
            for q in st.get("queue", []):
                if q["id"] == track_id:
                    target_index = q["pos"]
                    break
        except Exception as e:
            print(f"[播放] 读取队列失败: {e}")

        # 5. 跳转
        if target_index >= 0:
            try:
                self.command(["playlist", "index", str(target_index)])
                print(f"[播放] 已跳转到索引 {target_index} "
                      f"(track_id={track_id})")
                return
            except Exception as e:
                print(f"[播放] playlist index 跳转失败: {e}")

        # 6. 兜底：让 LMS 自己按 track_id 播放
        try:
            self.command(["playlist", "play", f"track_id:{track_id}"])
            print(f"[播放] 兜底 playlist play track_id={track_id}")
        except Exception as e:
            print(f"[播放] 兜底失败: {e}")
            self._play_single(track_id)

    def _play_single(self, track_id):
        """最终兜底：只加载单曲"""
        try:
            res = self.command(["playlistcontrol", "cmd:load",
                                f"track_id:{track_id}"])
            print(f"[播放] 单曲 load track_id={track_id} → {res}")
        except Exception as e:
            print(f"[播放] 单曲加载失败: {e}")

    # ---------- 音轨信息 / 歌词 ----------
    def get_track_url(self, track_id):
        try:
            res = self.command(["songinfo", 0, 100,
                                f"track_id:{track_id}", "tags:u"])
            for it in res.get("songinfo_loop", []):
                if "url" in it:
                    return it["url"]
        except Exception:
            pass
        return ""

    def download_track(self, track_id, max_mb=None):
        try:
            limit = (max_mb or 20) * 1024 * 1024
        except Exception:
            limit = 20 * 1024 * 1024
        for url in (f"http://{self.host}:{self.port}/music/{track_id}/download",
                    f"http://{self.host}:{self.port}/music/{track_id}/download.mp3",
                    f"http://{self.host}:{self.port}/music/{track_id}/file.mp3"):
            try:
                req = urllib.request.Request(
                    url, headers={"Range": f"bytes=0-{limit - 1}"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.read(limit)
            except Exception:
                continue
        return None

    def get_mai_lyrics(self, track_id, artist="", title="", album="",
                       duration=0, want_timestamps=True):
        cmd = ["musicartistinfo", "lyrics"]
        if track_id:
            cmd.append(f"track_id:{track_id}")
        if artist:
            cmd.append(f"artist:{artist}")
        if title:
            cmd.append(f"title:{title}")
        if album:
            cmd.append(f"album:{album}")
        if duration:
            cmd.append(f"duration:{int(duration)}")
        if want_timestamps:
            cmd.append("timestamps:1")
        try:
            res = self.command(cmd, player="-")
            text = res.get("lyrics", "") or ""
            err = res.get("error", "") or ""
            text = text.strip()
            if not text:
                return None, err or "MAI未找到"
            if want_timestamps and not LrcParser.has_timestamps(text):
                return None, "MAI歌词无时间轴"
            return text, ""
        except Exception as e:
            return None, str(e)

    # 在 lms_client.py 的 LmsClient 类中添加关机方法

    # 在 lms_client.py 的 LmsClient 类中修改此方法

    def shutdown_daphile(self):
        """关机：优先 Daphile Web CGI，失败自动回退 LMS stopserver。
           ★ 关键：Daphile 关机 CGI 是阻塞式的，会等待系统关机信号后才
             返回响应，很容易导致请求超时或连接被主动断开。这两种情况
             都应视为"请求已成功发出"，设备正在关机，不可再当失败处理。
           返回 True 表示关机请求已发出（不代表设备已完全断电）。"""
        urls = [
            f"http://{self.host}/cgi-bin/Info?ACTION=shutdown",
            f"http://{self.host}/Daphile/cgi-bin/Info?ACTION=shutdown",
        ]
        # 完整浏览器头：UA 太短会被 Daphile 判断为非法请求而静默忽略
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
            "Accept": ("text/html,application/xhtml+xml,application/xml;"
                       "q=0.9,image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "close",
        }

        try:
            import requests as _requests
        except ImportError:
            _requests = None

        if _requests is not None:
            sess = _requests.Session()
            # 预热：访问 Info 主页建立会话（部分 Daphile 版本要求带 cookie）
            try:
                sess.get(f"http://{self.host}/cgi-bin/Info",
                         headers=headers, timeout=4)
                print("[关机] Web 会话已建立")
            except Exception as e:
                print(f"[关机] 预热 Info 页面失败（忽略）: {e}")

            for url in urls:
                try:
                    print(f"[关机] GET {url}")
                    r = sess.get(url, headers={
                        **headers,
                        "Referer": f"http://{self.host}/cgi-bin/Info",
                    }, timeout=6)
                    print(f"[关机] 响应 status={r.status_code}")
                    if r.status_code == 200:
                        body = (r.text or "").strip()
                        print(f"[关机] 响应片段: {body[:120]!r}")
                        return True
                except _requests.exceptions.Timeout as e:
                    print(f"[关机] 请求超时（设备可能正在关机）: {e}")
                    return True
                except _requests.exceptions.ConnectionError as e:
                    print(f"[关机] 连接断开（设备可能正在关机）: {e}")
                    return True
                except Exception as e:
                    print(f"[关机] CGI 失败 {url}: {e}")
            sess.close()
        else:
            # requests 不可用，回退 urllib
            for url in urls:
                try:
                    print(f"[关机] GET {url} (urllib)")
                    req = urllib.request.Request(url, method="GET",
                                                 headers={
                        **headers,
                        "Referer": f"http://{self.host}/cgi-bin/Info",
                    })
                    with urllib.request.urlopen(req, timeout=6) as resp:
                        if resp.status == 200:
                            print(f"[关机] CGI 成功: {url}")
                            return True
                except urllib.error.HTTPError as e:
                    print(f"[关机] HTTP 错误 {e.code}: {url}")
                except urllib.error.URLError as e:
                    reason = getattr(e, "reason", None)
                    if isinstance(reason, (TimeoutError,
                                           ConnectionResetError,
                                           ConnectionRefusedError)):
                        print(f"[关机] 网络中断（可能设备正在关机）: {e}")
                        return True
                    print(f"[关机] URLError: {e}")
                except Exception as e:
                    print(f"[关机] 失败 {url}: {e}")

        # ---- 兜底 LMS stopserver ----
        try:
            print("[关机] CGI 无效，回退到 LMS stopserver …")
            res = self.command(["stopserver"], player="")
            print(f"[关机] stopserver 响应: {res}")
            return True
        except Exception as e:
            print(f"[关机] stopserver 也失败: {e}")
            return False

    def get_server_mac(self):
        """尝试通过LMS的serverstatus命令获取服务器MAC地址"""
        try:
            # 发送 serverstatus 请求，并请求包含MAC信息的标签（如 'm' 或 'all'）
            res = self.command(["serverstatus", 0, 50], player="-")
            # 在返回结果的根层级或 'server' 对象中查找 mac 字段
            mac = res.get("mac") or (res.get("server") or {}).get("mac")
            if mac:
                print(f"[MAC] LMS serverstatus 返回服务器MAC: {mac}")
                # 规范化格式
                clean = mac.replace(":", "").replace("-", "").replace(" ", "").upper()
                if len(clean) == 12:
                    return ":".join(clean[i:i+2] for i in range(0, 12, 2))
        except Exception as e:
            print(f"[MAC] LMS serverstatus 获取失败: {e}")
        return ""    