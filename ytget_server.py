#!/usr/bin/env python3
"""ytget companion server: HTTP bridge connecting Chrome Extension to ytget and FFmpeg."""

from __future__ import annotations

import argparse
import functools
import glob
import json
import os
import shutil
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yt_dlp
from yt_dlp.utils import sanitize_filename

SCRIPT_DIR = Path(__file__).resolve().parent

QUALITY_CHOICES: dict[str, int | str | None] = {
    "best": None,
    "4320": 4320,
    "2160": 2160,
    "1440": 1440,
    "1080": 1080,
    "720": 720,
    "480": 480,
    "360": 360,
    "240": 240,
    "144": 144,
    "audio": "audio",
}


def format_size(num_bytes: int | float) -> str:
    """Format bytes into a human-readable string (B, KB, MB, GB, TB)."""
    b = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024.0 or unit == "TB":
            return f"{b:.1f} {unit}" if unit != "B" else f"{int(b)} B"
        b /= 1024.0
    return f"{b:.1f} PB"


def format_duration(seconds: float) -> str:
    """Format duration in seconds into a clean human-readable string (e.g. '12m', '1m 24s', '45s')."""
    total_secs = max(0, int(round(seconds)))
    if total_secs < 60:
        return f"{total_secs}s"
    mins, secs = divmod(total_secs, 60)
    if mins < 60:
        return f"{mins}m {secs}s" if secs > 0 else f"{mins}m"
    hours, mins = divmod(mins, 60)
    return f"{hours}h {mins}m" if mins > 0 else f"{hours}h"


def ensure_ytdlp() -> None:
    if shutil.which("ffmpeg"):
        return
    print(
        "Note: ffmpeg is not on PATH. Videos may download as separate "
        "streams without merging, audio will not convert to MP3, and metadata/cover art embedding will be disabled.",
        file=sys.stderr,
    )


def find_runtime(name: str) -> str | None:
    path = shutil.which(name)
    if path:
        return path
    if name != "node":
        return None

    nvm_dirs: list[str] = []
    if os.environ.get("NVM_DIR"):
        nvm_dirs.append(os.environ["NVM_DIR"])
    xdg_config = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    nvm_dirs.append(os.path.join(xdg_config, "nvm"))
    nvm_dirs.append(os.path.expanduser("~/.nvm"))

    for nvm_dir in nvm_dirs:
        pattern = os.path.join(nvm_dir, "versions", "node", "*", "bin", "node")
        candidates = sorted(glob.glob(pattern), reverse=True)
        for candidate in candidates:
            if os.access(candidate, os.X_OK):
                return candidate
    return None


@functools.lru_cache(maxsize=1)
def youtube_js_opts() -> dict:
    """Options so YouTube can resolve formats (avoids 403 / missing streams)."""
    opts: dict = {}
    try:
        import yt_dlp_ejs  # noqa: F401
    except ImportError:
        opts["remote_components"] = {"ejs:github"}

    runtimes: dict = {}
    for name in ("node", "deno", "bun"):
        path = find_runtime(name)
        if path:
            runtimes[name] = {"path": path}
    if runtimes:
        opts["js_runtimes"] = runtimes
    else:
        print(
            "Warning: no Node.js, Deno, or Bun found. YouTube downloads may "
            "fail (HTTP 403). Install node or run `nvm use` so node is on PATH.",
            file=sys.stderr,
        )

    opts["http_chunk_size"] = 10485760
    opts["concurrent_fragment_downloads"] = 4
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        opts["impersonate"] = ImpersonateTarget.from_str("chrome")
    except Exception:
        pass
    return opts


def output_path(out_dir: Path, index: int = 1, total: int = 0, *, is_playlist: bool = True) -> Path:
    if is_playlist:
        pad_width = max(2, len(str(total))) if total > 0 else 2
        return out_dir / f"{index:0{pad_width}d} - %(title)s.%(ext)s"
    return out_dir / "%(title)s.%(ext)s"


def already_downloaded(
    out_dir: Path,
    index: int = 1,
    total: int = 0,
    *,
    is_playlist: bool = True,
    is_audio: bool = False,
    entry: dict | None = None,
) -> Path | None:
    if not out_dir.exists():
        return None

    if is_audio:
        valid_exts = {".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav", ".aac"}
    else:
        valid_exts = {".mp4", ".mkv", ".webm", ".m4a"}

    if is_playlist:
        pad_width = max(2, len(str(total))) if total > 0 else 2
        prefixes = (f"{index:0{pad_width}d} - ", f"{index:02d} - ")
        for path in out_dir.iterdir():
            if not path.is_file():
                continue
            if any(path.name.endswith(ext) for ext in (".part", ".ytdl", ".temp")) or ".temp." in path.name:
                continue
            if any(path.name.startswith(p) for p in prefixes) and path.suffix.lower() in valid_exts:
                try:
                    if path.stat().st_size > 0:
                        return path
                except OSError:
                    continue
        return None

    # Single video in download folder
    title = (entry or {}).get("title")
    video_id = (entry or {}).get("id")
    candidate_stems: set[str] = set()
    if title:
        sanitized = sanitize_filename(title)
        candidate_stems.add(sanitized)
        candidate_stems.add(f"01 - {sanitized}")

    for path in out_dir.iterdir():
        if not path.is_file():
            continue
        if any(path.name.endswith(ext) for ext in (".part", ".ytdl", ".temp")) or ".temp." in path.name:
            continue
        if path.suffix.lower() in valid_exts:
            try:
                if path.stat().st_size <= 0:
                    continue
            except OSError:
                continue
            if candidate_stems and (path.stem in candidate_stems or any(path.stem.startswith(s) for s in candidate_stems)):
                return path
            if video_id and video_id in path.name:
                return path
    return None


def is_single_video_url(url: str) -> bool:
    lower = url.lower()
    if "list=" in lower or "/playlist" in lower:
        return False
    single_patterns = ("watch?v=", "youtu.be/", "/shorts/", "/live/", "/embed/", "/v/")
    return any(p in lower for p in single_patterns)


def format_selectors(max_height: int | str | None) -> list[str]:
    if max_height == "audio":
        primary = "bestaudio/best"
        fallback = "ba/b"
        final_fallback = "best"
    elif max_height is None:
        primary = "bestvideo+bestaudio/best"
        fallback = "best"
        final_fallback = "bestvideo+bestaudio/best"
    else:
        h = max_height
        primary = (
            f"bestvideo[height<={h}]+bestaudio/"
            f"best[height<={h}]"
        )
        fallback = f"best[height<={h}]/bestvideo+bestaudio/best"
        final_fallback = f"best[height<={h}]/best"
    return [primary, fallback, final_fallback]


class YtLogger:
    """Controls yt-dlp logging output. Silences extractor chatter in normal mode."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.last_error: str | None = None

    def debug(self, msg: str):
        if self.verbose:
            print(msg)

    def info(self, msg: str):
        if self.verbose:
            print(msg)

    def warning(self, msg: str):
        if self.verbose:
            print(f"WARNING: {msg}", file=sys.stderr)
        else:
            ignored = (
                "Some ios client",
                "Some android client",
                "Neither mutagen nor",
                "n challenge solving",
                "Sleeping",
            )
            if any(ign in msg for ign in ignored):
                return
            if any(k in msg for k in ("403", "429", "Forbidden", "Too Many Requests")):
                self.last_error = msg

    def error(self, msg: str):
        clean = msg[7:] if msg.startswith("ERROR: ") else msg
        self.last_error = clean
        if self.verbose:
            print(f"ERROR: {clean}", file=sys.stderr)


def attempt_download(video_url: str, ydl_opts: dict) -> bool:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ret = ydl.download([video_url])
        return ret == 0


def build_download_opts(
    target_dir: Path,
    quality_val: int | str | None,
    index: int = 1,
    total: int = 1,
    *,
    is_playlist: bool = True,
    playlist_title: str | None = None,
    embed_thumbnail: bool = True,
    cookies_from_browser: str | None = None,
    cookies_file: Path | None = None,
    has_ffmpeg: bool | None = None,
    verbose: bool = False,
    progress_display: Any | None = None,
    yt_logger: YtLogger | None = None,
) -> dict:
    if has_ffmpeg is None:
        has_ffmpeg = bool(shutil.which("ffmpeg"))

    is_audio = (quality_val == "audio")
    opts: dict = {
        **youtube_js_opts(),
        "outtmpl": str(output_path(target_dir, index, total, is_playlist=is_playlist)),
        "noplaylist": True,
        "quiet": not verbose,
        "no_warnings": not verbose,
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "sleep_interval": 1,
        "max_sleep_interval": 5,
        "ignoreerrors": True,
        "logger": yt_logger if yt_logger is not None else YtLogger(verbose=verbose),
    }

    if progress_display and not verbose:
        opts["progress_hooks"] = [progress_display.progress_hook]
        opts["postprocessor_hooks"] = [progress_display.postprocessor_hook]

    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser, None, None, None)
    if cookies_file:
        opts["cookiefile"] = str(cookies_file)

    if has_ffmpeg:
        postprocessors: list[dict] = []
        if is_audio:
            postprocessors.append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            })
        else:
            opts["merge_output_format"] = "mp4"

        postprocessors.append({
            "key": "FFmpegMetadata",
            "add_chapters": True,
            "add_metadata": True,
        })

        metadata_args: list[str] = []
        if is_playlist and playlist_title:
            metadata_args.extend(["-metadata", f"album={playlist_title}"])
        if is_playlist and total > 0:
            metadata_args.extend(["-metadata", f"track={index}/{total}"])
        elif is_playlist and index > 0:
            metadata_args.extend(["-metadata", f"track={index}"])

        if metadata_args:
            opts["postprocessor_args"] = {"metadata": metadata_args}

        if embed_thumbnail:
            opts["writethumbnail"] = True
            postprocessors.append({
                "key": "EmbedThumbnail",
                "already_have_thumbnail": False,
            })

        if postprocessors:
            opts["postprocessors"] = postprocessors

    return opts


def fetch_info(
    url: str,
    *,
    no_playlist: bool = False,
    cookies_from_browser: str | None = None,
    cookies_file: Path | None = None,
    verbose: bool = False,
) -> tuple[bool, str, list[dict]]:
    """Distinguish whether URL is a single video or a playlist and extract metadata.

    Returns (is_playlist, title, entries).
    """
    cookie_opts: dict = {}
    if cookies_from_browser:
        cookie_opts["cookiesfrombrowser"] = (cookies_from_browser, None, None, None)
    if cookies_file:
        cookie_opts["cookiefile"] = str(cookies_file)

    common_opts = {
        **youtube_js_opts(),
        **cookie_opts,
        "quiet": not verbose,
        "no_warnings": not verbose,
        "logger": YtLogger(verbose=verbose),
    }

    if no_playlist or is_single_video_url(url):
        ydl_opts = {
            **common_opts,
            "skip_download": True,
            "noplaylist": True,
        }
        for attempt in range(2):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                if info:
                    title = info.get("title") or "video"
                    canonical_url = (
                        info.get("webpage_url")
                        or (f"https://www.youtube.com/watch?v={info['id']}" if info.get("id") else url)
                    )
                    entry_item = dict(info)
                    entry_item["url"] = canonical_url
                    entry_item["webpage_url"] = canonical_url
                    return False, title, [entry_item]
            except yt_dlp.utils.DownloadError:
                if attempt == 0:
                    time.sleep(1)
                    continue
        return False, "video", [{"url": url, "webpage_url": url}]

    # Potential playlist URL
    ydl_opts = {
        **common_opts,
        "extract_flat": "in_playlist",
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        print(f"Error fetching playlist info: {exc}", file=sys.stderr)
        return False, "", []

    if not info:
        return False, "", []

    if info.get("_type") == "playlist" or ("entries" in info and info["entries"] is not None):
        title = info.get("title") or "YouTube Playlist"
        raw_entries = info.get("entries") or []
        entries = [e for e in raw_entries if e]
        return True, title, entries

    # Resolved to a single video
    title = info.get("title") or "video"
    return False, title, [info]


PROBE_CACHE: dict[str, tuple[float, dict]] = {}
PROBE_CACHE_TTL = 300.0  # 5 minutes cache


def probe_url(url: str) -> dict:
    """Inspects a YouTube video or playlist URL and returns available resolutions,
    estimated file sizes (in MB), and metadata without downloading.
    Results are cached in memory for fast repeated lookups.
    """
    now = time.time()
    clean_url = url.strip()
    if "watch?v=" in clean_url or "youtu.be/" in clean_url:
        parsed = urlparse(clean_url)
        qs = parse_qs(parsed.query)
        v_id = qs.get("v", [""])[0]
        if v_id:
            clean_url = f"https://www.youtube.com/watch?v={v_id}"

    cache_key = clean_url
    if cache_key in PROBE_CACHE:
        cached_time, cached_data = PROBE_CACHE[cache_key]
        if now - cached_time < PROBE_CACHE_TTL:
            return cached_data

    is_single = is_single_video_url(url)
    opts = dict(youtube_js_opts())
    opts.update({
        "noplaylist": is_single,
        "extract_flat": "in_playlist" if not is_single else False,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 15,
    })

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(clean_url, download=False, process=True)
    except Exception as exc:
        return {"error": f"Failed to probe URL: {exc}", "qualities": []}

    if not info:
        return {"error": "Could not extract media info", "qualities": []}

    is_playlist = ("entries" in info or info.get("_type") == "playlist") and not is_single
    if is_playlist:
        raw_entries = info.get("entries") or []
        entries = [e for e in raw_entries if e]
        title = info.get("title") or "YouTube Playlist"
        qualities = [
            {"id": "best", "label": "⚡ Best Available", "desc": "Highest available resolution for each video", "size_str": ""},
            {"id": "1080", "label": "🎬 1080p Full HD", "desc": "Standard 1080p MP4", "size_str": ""},
            {"id": "720", "label": "📺 720p HD", "desc": "Fast download & smaller file", "size_str": ""},
            {"id": "480", "label": "📱 480p SD", "desc": "Standard definition", "size_str": ""},
            {"id": "360", "label": "💾 360p Low", "desc": "Smallest video size", "size_str": ""},
            {"id": "audio", "label": "🎵 Audio Only (MP3)", "desc": "High-quality MP3 with cover art & tags", "size_str": ""},
        ]
        res = {
            "id": info.get("id"),
            "title": title,
            "is_playlist": True,
            "count": len(entries),
            "qualities": qualities,
        }
        PROBE_CACHE[cache_key] = (now, res)
        return res

    # Single video handling
    title = info.get("title") or "YouTube Video"
    duration = info.get("duration") or 0
    formats = info.get("formats", [])

    # 1. Determine best audio size
    best_audio_size = 0
    for f in formats:
        if f.get("vcodec") == "none" and f.get("acodec") != "none":
            sz = f.get("filesize") or f.get("filesize_approx")
            if not sz and duration:
                abr = f.get("abr") or 128
                sz = int(abr * 1000 / 8 * duration)
            if sz and sz > best_audio_size:
                best_audio_size = sz

    # 2. Group available video formats by height
    height_map: dict[int, dict] = {}
    for f in formats:
        h = f.get("height")
        if not h or f.get("vcodec") == "none":
            continue
        sz = f.get("filesize") or f.get("filesize_approx")
        if not sz and duration:
            tbr = f.get("tbr") or f.get("vbr")
            if tbr:
                sz = int(tbr * 1000 / 8 * duration)
        if h not in height_map or (sz and sz > (height_map[h].get("filesize") or 0)):
            height_map[h] = {
                "filesize": sz or 0,
                "fps": f.get("fps"),
                "vcodec": f.get("vcodec"),
                "ext": f.get("ext"),
            }

    available_heights = sorted(height_map.keys(), reverse=True)
    qualities = []

    label_map = {
        4320: "8K Ultra HD",
        2160: "4K Ultra HD",
        1440: "2K Quad HD",
        1080: "1080p Full HD",
        720: "720p HD",
        480: "480p SD",
        360: "360p Medium",
        240: "240p Low",
        144: "144p Tiny",
    }

    # Best available option
    if available_heights:
        max_h = available_heights[0]
        max_v_sz = height_map[max_h]["filesize"]
        max_tot = max_v_sz + best_audio_size if max_v_sz > 0 else 0
        best_size_str = format_size(max_tot) if max_tot > 0 else ""
        qualities.append({
            "id": "best",
            "height": max_h,
            "label": f"⚡ Best Available ({max_h}p)",
            "size_str": best_size_str,
            "size_bytes": max_tot,
            "desc": f"Highest available resolution ({max_h}p)",
        })

    # ONLY add qualities that actually exist for this video!
    for h in available_heights:
        v_sz = height_map[h]["filesize"]
        tot = v_sz + best_audio_size if v_sz > 0 else 0
        size_str = format_size(tot) if tot > 0 else ""
        name = label_map.get(h, f"{h}p")
        prefix = "🎬 " if h >= 1080 else ("📺 " if h >= 720 else "📱 ")
        qualities.append({
            "id": str(h),
            "height": h,
            "label": f"{prefix}{name}",
            "size_str": size_str,
            "size_bytes": tot,
            "desc": f"{h}p video stream with audio",
        })

    # Audio option
    audio_size_str = format_size(best_audio_size) if best_audio_size > 0 else ""
    qualities.append({
        "id": "audio",
        "height": None,
        "label": "🎵 Audio Only (MP3)",
        "size_str": audio_size_str,
        "size_bytes": best_audio_size,
        "desc": "High-quality MP3 with cover art & tags",
    })

    res = {
        "id": info.get("id"),
        "title": title,
        "duration": duration,
        "duration_str": format_duration(duration) if duration else "",
        "is_playlist": False,
        "available_heights": available_heights,
        "qualities": qualities,
    }
    PROBE_CACHE[cache_key] = (now, res)
    return res


class JobStatus:
    QUEUED = "queued"
    FETCHING = "fetching"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadJob:
    """Represents an active or historical download task."""

    def __init__(
        self,
        job_id: str,
        url: str,
        quality: str = "best",
        skip_existing: bool = True,
        embed_thumbnail: bool = True,
        output_dir: Path | None = None,
    ):
        self.job_id = job_id
        self.url = url
        self.quality = quality if quality in QUALITY_CHOICES else "best"
        self.quality_val = QUALITY_CHOICES[self.quality]
        self.is_audio = self.quality == "audio"
        self.skip_existing = skip_existing
        self.embed_thumbnail = embed_thumbnail
        self.output_dir = output_dir or (SCRIPT_DIR / "downloads")

        self.status = JobStatus.QUEUED
        self.title: str = ""
        self.thumbnail_url: str = ""
        self.is_playlist: bool = False
        self.total_items: int = 1
        self.completed_items: int = 0
        self.current_item_index: int = 1
        self.current_item_title: str = ""
        self.item_progress: float = 0.0
        self.item_downloaded_bytes: int = 0
        self.item_total_bytes: int = 0
        self.speed_str: str = ""
        self.eta_str: str = ""
        self.phase: str = "queued"
        self.error: str | None = None

        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()
        self._lock = threading.RLock()

    def pause(self) -> bool:
        with self._lock:
            if self.status in (JobStatus.DOWNLOADING, JobStatus.FETCHING, JobStatus.QUEUED):
                self.status = JobStatus.PAUSED
                self.phase = "Paused"
                self.speed_str = "Paused"
                self.pause_event.clear()
                return True
            return False

    def resume(self) -> bool:
        with self._lock:
            if self.status == JobStatus.PAUSED:
                self.status = JobStatus.DOWNLOADING
                self.phase = f"Resuming download ({self.item_progress:.1f}%)..."
                self.pause_event.set()
                return True
            return False

    def cancel(self) -> bool:
        with self._lock:
            self.cancel_event.set()
            self.pause_event.set()
            self.status = JobStatus.CANCELLED
            self.phase = "Cancelled by user"
            self.speed_str = "0 B/s"
            self.eta_str = "--"
            if not self.finished_at:
                self.finished_at = time.time()
            return True

    def overall_progress(self) -> float:
        with self._lock:
            if self.status == JobStatus.COMPLETED:
                return 100.0
            if self.total_items <= 0:
                return 0.0
            done = self.completed_items
            current_fraction = (self.item_progress / 100.0) if self.status in (JobStatus.DOWNLOADING, JobStatus.PAUSED) else 0.0
            pct = ((done + current_fraction) / self.total_items) * 100.0
            return max(0.0, min(100.0, round(pct, 1)))

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            elapsed = (time.time() - self.started_at) if self.started_at else 0.0
            if self.finished_at and self.started_at:
                elapsed = self.finished_at - self.started_at

            return {
                "job_id": self.job_id,
                "url": self.url,
                "quality": self.quality,
                "is_audio": self.is_audio,
                "status": self.status,
                "phase": self.phase,
                "title": self.title,
                "thumbnail_url": self.thumbnail_url,
                "is_playlist": self.is_playlist,
                "total_items": self.total_items,
                "completed_items": self.completed_items,
                "current_item_index": self.current_item_index,
                "current_item_title": self.current_item_title,
                "item_progress": round(self.item_progress, 1),
                "item_downloaded_bytes": self.item_downloaded_bytes,
                "item_total_bytes": self.item_total_bytes,
                "item_downloaded_str": format_size(self.item_downloaded_bytes) if self.item_downloaded_bytes else "0 B",
                "item_total_str": format_size(self.item_total_bytes) if self.item_total_bytes else "0 B",
                "speed": self.speed_str,
                "eta": self.eta_str,
                "overall_progress": self.overall_progress(),
                "elapsed_str": format_duration(elapsed),
                "error": self.error,
                "created_at": self.created_at,
                "output_dir": str(self.output_dir),
            }


class JobManager:
    """Thread-safe manager for download jobs and background workers."""

    def __init__(self, max_concurrent: int = 10, auto_start: bool = False):
        self.jobs: dict[str, DownloadJob] = {}
        self.job_order: list[str] = []
        self._queue: list[DownloadJob] = []
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self.max_concurrent = max(0, max_concurrent)
        self._running = False
        self._worker_threads: list[threading.Thread] = []

        if auto_start and self.max_concurrent > 0:
            self.start()

    def start(self) -> None:
        with self._lock:
            if self._running or self.max_concurrent <= 0:
                return
            self._running = True
            for i in range(self.max_concurrent):
                t = threading.Thread(target=self._worker_loop, name=f"ytget-worker-{i}", daemon=True)
                t.start()
                self._worker_threads.append(t)

    def stop(self) -> None:
        with self._condition:
            self._running = False
            self._condition.notify_all()
        for t in list(self._worker_threads):
            t.join(timeout=0.5)
        self._worker_threads.clear()

    def set_max_concurrent(self, count: int) -> int:
        count = max(1, min(int(count), 50))
        with self._condition:
            self.max_concurrent = count
            if self._running:
                if count > len(self._worker_threads):
                    for i in range(len(self._worker_threads), count):
                        t = threading.Thread(
                            target=self._worker_loop,
                            name=f"ytget-worker-{i}",
                            daemon=True,
                        )
                        t.start()
                        self._worker_threads.append(t)
                elif count < len(self._worker_threads):
                    self._condition.notify_all()
        return self.max_concurrent

    def enqueue_job(
        self,
        url: str,
        quality: str = "best",
        skip_existing: bool = True,
        embed_thumbnail: bool = True,
        output_dir: Path | None = None,
    ) -> DownloadJob:
        job_id = uuid.uuid4().hex[:8]
        job = DownloadJob(
            job_id=job_id,
            url=url,
            quality=quality,
            skip_existing=skip_existing,
            embed_thumbnail=embed_thumbnail,
            output_dir=output_dir,
        )
        with self._condition:
            self.jobs[job_id] = job
            self.job_order.append(job_id)
            self._queue.append(job)
            self._condition.notify()
        return job

    def get_job(self, job_id: str) -> DownloadJob | None:
        with self._lock:
            return self.jobs.get(job_id)

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            recent_ids = reversed(self.job_order[-limit:])
            return [self.jobs[jid].to_dict() for jid in recent_ids if jid in self.jobs]

    def pause_job(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            return job.pause()

    def resume_job(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            return job.resume()

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            ok = job.cancel()
            if job in self._queue:
                self._queue.remove(job)
            return ok

    def _worker_loop(self) -> None:
        while self._running:
            with self._condition:
                while self._running and not self._queue:
                    cur = threading.current_thread()
                    if len(self._worker_threads) > self.max_concurrent and cur in self._worker_threads:
                        self._worker_threads.remove(cur)
                        return
                    self._condition.wait(timeout=1.0)
                if not self._running:
                    break
                cur = threading.current_thread()
                if len(self._worker_threads) > self.max_concurrent and cur in self._worker_threads:
                    self._worker_threads.remove(cur)
                    return
                job = self._queue.pop(0)

            if job.cancel_event.is_set():
                continue

            self._execute_job(job)

    def _execute_job(self, job: DownloadJob) -> None:
        job.started_at = time.time()
        job.status = JobStatus.FETCHING
        job.phase = "Fetching video / playlist information..."

        try:
            is_pl, pl_or_video_title, entries = fetch_info(
                job.url,
                no_playlist=False,
                verbose=False,
            )

            if job.cancel_event.is_set():
                job.status = JobStatus.CANCELLED
                job.phase = "cancelled"
                job.finished_at = time.time()
                return

            if not entries:
                job.status = JobStatus.FAILED
                job.phase = "failed"
                job.error = "No downloadable videos found at this URL."
                job.finished_at = time.time()
                return

            job.is_playlist = is_pl
            job.title = pl_or_video_title or ("Playlist" if is_pl else "Video")
            job.total_items = len(entries)
            job.status = JobStatus.DOWNLOADING

            # Resolve output directory
            target_base = job.output_dir
            target_base.mkdir(parents=True, exist_ok=True)
            if is_pl:
                safe_folder = sanitize_filename(job.title or "YouTube Playlist", restricted=False).strip()
                dest_dir = target_base / (safe_folder or "YouTube Playlist")
                dest_dir.mkdir(parents=True, exist_ok=True)
            else:
                dest_dir = target_base

            for idx, entry in enumerate(entries, start=1):
                if job.cancel_event.is_set():
                    job.status = JobStatus.CANCELLED
                    job.phase = "cancelled"
                    job.finished_at = time.time()
                    return

                while not job.pause_event.is_set():
                    if job.cancel_event.is_set():
                        job.status = JobStatus.CANCELLED
                        job.phase = "cancelled"
                        job.finished_at = time.time()
                        return
                    time.sleep(0.2)

                job.current_item_index = idx
                job.current_item_title = entry.get("title") or f"Item {idx}"
                job.item_progress = 0.0
                job.item_downloaded_bytes = 0
                job.item_total_bytes = 0
                job.speed_str = ""
                job.eta_str = ""
                job.phase = f"Downloading item {idx}/{job.total_items}: {job.current_item_title}"

                # Extract video URL
                vid = entry.get("webpage_url") or entry.get("url") or entry.get("id")
                if not vid:
                    continue
                if not str(vid).startswith("http"):
                    vid = f"https://www.youtube.com/watch?v={vid}"

                # Handle deleted / private
                item_title = entry.get("title") or ""
                if item_title in ("[Private video]", "[Deleted video]"):
                    job.completed_items += 1
                    continue

                # Check skip existing
                if job.skip_existing:
                    existing = already_downloaded(
                        dest_dir,
                        idx,
                        job.total_items,
                        is_playlist=is_pl,
                        is_audio=job.is_audio,
                        entry=entry,
                    )
                    if existing:
                        job.completed_items += 1
                        job.item_progress = 100.0
                        continue

                # Run download for this single video
                success = self._download_single_item(job, vid, dest_dir, idx, job.total_items, entry)
                if job.cancel_event.is_set():
                    job.status = JobStatus.CANCELLED
                    job.phase = "Cancelled by user"
                    job.speed_str = "0 B/s"
                    if not job.finished_at:
                        job.finished_at = time.time()
                    return

                if success:
                    job.completed_items += 1
                else:
                    if not is_pl:
                        job.status = JobStatus.FAILED
                        job.phase = "failed"
                        job.error = job.error or "Failed to download video stream."
                        job.finished_at = time.time()
                        return

            if job.cancel_event.is_set():
                job.status = JobStatus.CANCELLED
                job.phase = "Cancelled by user"
                job.speed_str = "0 B/s"
                if not job.finished_at:
                    job.finished_at = time.time()
                return

            job.status = JobStatus.COMPLETED
            job.phase = "All downloads complete!"
            job.item_progress = 100.0
            job.finished_at = time.time()

        except (KeyboardInterrupt, SystemExit):
            job.status = JobStatus.CANCELLED
            job.phase = "Cancelled by user"
            job.speed_str = "0 B/s"
            if not job.finished_at:
                job.finished_at = time.time()
            return
        except Exception as exc:
            if job.cancel_event.is_set():
                job.status = JobStatus.CANCELLED
                job.phase = "Cancelled by user"
                job.speed_str = "0 B/s"
            else:
                job.status = JobStatus.FAILED
                job.phase = "failed"
                job.error = str(exc)
            if not job.finished_at:
                job.finished_at = time.time()

    def _download_single_item(
        self,
        job: DownloadJob,
        vid_url: str,
        dest_dir: Path,
        index: int,
        total: int,
        entry: dict,
    ) -> bool:
        """Download a single video or audio stream with live progress updates."""
        server_logger = YtLogger(verbose=False)

        def progress_hook(d: dict) -> None:
            if job.cancel_event.is_set():
                raise KeyboardInterrupt("Cancelled by user")

            while not job.pause_event.is_set():
                if job.cancel_event.is_set():
                    raise KeyboardInterrupt("Cancelled by user")
                time.sleep(0.2)

            status = d.get("status")
            if status == "downloading":
                downloaded = d.get("downloaded_bytes", 0)
                tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                speed = d.get("speed")
                eta = d.get("eta")

                with job._lock:
                    job.item_downloaded_bytes = int(downloaded)
                    job.item_total_bytes = int(tot)
                    if tot > 0:
                        job.item_progress = (downloaded / tot) * 100.0
                    if speed:
                        job.speed_str = f"{format_size(speed)}/s"
                    if eta is not None:
                        job.eta_str = f"ETA {format_duration(eta)}"
                    job.phase = f"Downloading stream ({job.item_progress:.1f}%)"

            elif status == "finished":
                with job._lock:
                    job.item_progress = 100.0
                    job.phase = "Processing & finalizing media file..."

        def postprocessor_hook(d: dict) -> None:
            if job.cancel_event.is_set():
                raise KeyboardInterrupt("Cancelled by user")
            status = d.get("status")
            pp = str(d.get("postprocessor", "")).lower()
            if status == "started":
                msg = "Processing file..."
                if "merger" in pp:
                    msg = "Merging audio & video streams..."
                elif "extractaudio" in pp:
                    msg = "Converting audio to high-quality MP3..."
                elif "thumbnail" in pp:
                    msg = "Embedding thumbnail artwork..."
                elif "metadata" in pp:
                    msg = "Embedding metadata tags..."
                with job._lock:
                    job.phase = msg

        base_opts = build_download_opts(
            dest_dir,
            job.quality_val,
            index,
            total,
            is_playlist=job.is_playlist,
            playlist_title=job.title,
            embed_thumbnail=job.embed_thumbnail,
            verbose=False,
            progress_display=None,
            yt_logger=server_logger,
        )
        base_opts["progress_hooks"] = [progress_hook]
        base_opts["postprocessor_hooks"] = [postprocessor_hook]

        selectors = format_selectors(job.quality_val)
        for attempt, fmt in enumerate(selectors, start=1):
            if job.cancel_event.is_set():
                return False
            while not job.pause_event.is_set():
                if job.cancel_event.is_set():
                    return False
                time.sleep(0.2)
            ydl_opts = {**base_opts, "format": fmt}
            try:
                with job._lock:
                    job.phase = f"Connecting to stream (attempt {attempt})..."
                ok = attempt_download(vid_url, ydl_opts)
                if ok:
                    return True
            except (KeyboardInterrupt, SystemExit):
                return False
            except Exception as err:
                with job._lock:
                    job.error = str(err)

            if job.cancel_event.is_set():
                return False
            if attempt < len(selectors):
                for _ in range(15):
                    if job.cancel_event.is_set():
                        return False
                    time.sleep(0.1)

        return False


# Global job manager instance
job_manager = JobManager(max_concurrent=10)


class YtGetRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler supporting CORS and ytget API endpoints."""

    server_version = "ytget-server/1.0"

    def _set_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

    def _send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        encoded = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(encoded)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        if path in ("", "/status", "/health"):
            active = sum(1 for j in job_manager.jobs.values() if j.status in (JobStatus.QUEUED, JobStatus.FETCHING, JobStatus.DOWNLOADING))
            self._send_json({
                "status": "ok",
                "service": "ytget",
                "version": "1.0.0",
                "active_jobs": active,
                "total_jobs": len(job_manager.jobs),
                "max_concurrent": job_manager.max_concurrent,
                "server_time": time.time(),
            })

        elif path == "/settings":
            self._send_json({
                "max_concurrent": job_manager.max_concurrent,
            })

        elif path in ("/tasks", "/jobs"):
            limit = int(query.get("limit", [20])[0])
            jobs_data = job_manager.list_jobs(limit=limit)
            self._send_json({"jobs": jobs_data})

        elif path.startswith("/tasks/") or path.startswith("/jobs/"):
            parts = path.split("/")
            job_id = parts[2] if len(parts) > 2 else ""
            job = job_manager.get_job(job_id)
            if job:
                self._send_json(job.to_dict())
            else:
                self._send_json({"error": f"Job '{job_id}' not found"}, status=HTTPStatus.NOT_FOUND)

        elif path == "/downloads":
            # List completed files in downloads/ directory
            dl_dir = SCRIPT_DIR / "downloads"
            files_list = []
            if dl_dir.exists():
                for p in sorted(dl_dir.rglob("*"), key=os.path.getmtime, reverse=True):
                    if p.is_file() and not p.name.startswith("."):
                        files_list.append({
                            "name": p.name,
                            "relative_path": str(p.relative_to(dl_dir)),
                            "size": p.stat().st_size,
                            "size_str": format_size(p.stat().st_size),
                            "modified": p.stat().st_mtime,
                        })
            self._send_json({"downloads": files_list[:30]})

        elif path == "/probe":
            target_url = query.get("url", [""])[0].strip()
            if not target_url:
                self._send_json({"error": "Missing 'url' query parameter"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                data = probe_url(target_url)
                self._send_json({"status": "ok", **data})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        else:
            self._send_json({"error": "Endpoint not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON body"}, status=HTTPStatus.BAD_REQUEST)
            return

        if path == "/download":
            url = payload.get("url", "").strip()
            if not url:
                self._send_json({"error": "Missing 'url' parameter in request body"}, status=HTTPStatus.BAD_REQUEST)
                return

            quality = payload.get("quality", "best")
            skip_existing = bool(payload.get("skip_existing", True))
            embed_thumbnail = bool(payload.get("embed_thumbnail", True))

            custom_out = payload.get("output_dir")
            out_path = Path(custom_out).expanduser().resolve() if custom_out else None

            job = job_manager.enqueue_job(
                url=url,
                quality=quality,
                skip_existing=skip_existing,
                embed_thumbnail=embed_thumbnail,
                output_dir=out_path,
            )

            self._send_json(
                {
                    "status": "accepted",
                    "message": "Download queued successfully",
                    "job_id": job.job_id,
                    "url": job.url,
                    "quality": job.quality,
                },
                status=HTTPStatus.ACCEPTED,
            )

        elif path == "/probe":
            url = payload.get("url", "").strip()
            if not url:
                self._send_json({"error": "Missing 'url' parameter in request body"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                data = probe_url(url)
                self._send_json({"status": "ok", **data})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        elif path.startswith("/pause/"):
            parts = path.split("/")
            job_id = parts[2] if len(parts) > 2 else ""
            success = job_manager.pause_job(job_id)
            if success:
                self._send_json({"status": "paused", "job_id": job_id})
            else:
                self._send_json({"error": f"Job '{job_id}' not found or cannot be paused"}, status=HTTPStatus.NOT_FOUND)

        elif path.startswith("/resume/"):
            parts = path.split("/")
            job_id = parts[2] if len(parts) > 2 else ""
            success = job_manager.resume_job(job_id)
            if success:
                self._send_json({"status": "resumed", "job_id": job_id})
            else:
                self._send_json({"error": f"Job '{job_id}' not found or cannot be resumed"}, status=HTTPStatus.NOT_FOUND)

        elif path.startswith("/cancel/"):
            parts = path.split("/")
            job_id = parts[2] if len(parts) > 2 else ""
            success = job_manager.cancel_job(job_id)
            if success:
                self._send_json({"status": "cancelled", "job_id": job_id})
            else:
                self._send_json({"error": f"Job '{job_id}' not found"}, status=HTTPStatus.NOT_FOUND)

        elif path == "/settings":
            max_c = payload.get("max_concurrent")
            if max_c is not None:
                try:
                    val = int(max_c)
                    job_manager.set_max_concurrent(val)
                except (ValueError, TypeError):
                    pass
            self._send_json({
                "status": "ok",
                "max_concurrent": job_manager.max_concurrent,
            })

        else:
            self._send_json({"error": "Endpoint not found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        """Custom concise logging for HTTP requests."""
        # Suppress noisy health checks from clogging terminal output
        if args and ("GET /status" in str(args[0]) or "GET /health" in str(args[0]) or "GET /tasks" in str(args[0])):
            return
        sys.stdout.write(f"[ytget-server] {self.address_string()} - {format % args}\n")
        sys.stdout.flush()


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    ensure_ytdlp()
    job_manager.start()
    server_address = (host, port)
    try:
        httpd = ThreadingHTTPServer(server_address, YtGetRequestHandler)
    except OSError as err:
        print(f"Error starting ytget server on {host}:{port}: {err}", file=sys.stderr)
        print(f"Port {port} might already be in use. Try specifying another port with --port.", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("           🚀 ytget Local Companion Server Active            ")
    print("=" * 60)
    print(f"  • Listening on: http://{host}:{port}")
    print("  • Chrome Extension status: Ready to receive downloads")
    print(f"  • Downloads folder: {SCRIPT_DIR / 'downloads'}")
    print("  • Press Ctrl + C to stop the server")
    print("=" * 60)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ytget server...")
    finally:
        job_manager.stop()
        httpd.server_close()
        print("Server stopped cleanly.")


def main() -> None:
    parser = argparse.ArgumentParser(description="ytget local companion server for Chrome Extension")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on (default: 8765)")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
