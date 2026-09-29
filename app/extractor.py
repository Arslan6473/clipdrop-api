"""yt-dlp wrapper. Public content only: no cookies, no credentials, no config files, no generic extractor."""

import logging
import os
import re
import shutil
import threading
import time
import unicodedata
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

from .config import settings
from .errors import ApiError, map_ytdlp_error
from .formats import build_formats, selector_for, video_info
from .jobs import Job, JobStore
from .validation import PLATFORM_NAMES

log = logging.getLogger("clipdrop.extractor")

CONTENT_TYPES = {
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
    "mov": "video/quicktime",
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "ogg": "audio/ogg",
}

_download_slots = threading.BoundedSemaphore(settings.max_concurrent_downloads)


def can_merge() -> bool:
    return shutil.which("ffmpeg") is not None


class _Logger:
    """Keeps yt-dlp quiet; errors surface as exceptions and are mapped to safe codes."""

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        log.info("yt-dlp: %s", str(msg)[:300])


def _base_options() -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "retries": 2,
        "extractor_retries": 1,
        "cachedir": False,
        "logger": _Logger(),
        # Never fall back to the generic extractor: it would fetch arbitrary pages (SSRF).
        "allowed_extractors": ["default", "-generic"],
        # Deliberately absent: cookiefile, cookiesfrombrowser, username, password, usenetrc.
    }


def _single(info: dict[str, Any]) -> dict[str, Any]:
    """Posts with several videos (e.g. Instagram carousels) come back as playlists: use the first."""
    if info.get("_type") in ("playlist", "multi_video"):
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise ApiError("NOT_FOUND", "empty playlist")
        return entries[0]
    return info


def extract(url: str) -> dict[str, Any]:
    try:
        with yt_dlp.YoutubeDL(_base_options()) as ydl:
            info = ydl.extract_info(url, download=False)
    except (DownloadError, ExtractorError) as err:
        raise ApiError(map_ytdlp_error(str(err)), "extract failed") from None
    if not info:
        raise ApiError("NOT_FOUND")
    info = _single(ydl.sanitize_info(info))
    if info.get("is_live"):
        raise ApiError("DOWNLOADS_UNAVAILABLE", "live stream")
    return info


def analyze(url: str, platform: str) -> dict[str, Any]:
    info = extract(url)
    formats = build_formats(info, can_merge())
    return {
        "success": True,
        "video": video_info(info, platform, PLATFORM_NAMES[platform], url),
        "formats": formats,
        "notice": None if formats else "Downloads aren't currently available for this source.",
    }


def sanitize_filename(name: str, ext: str) -> str:
    ext = ext.lower() if re.fullmatch(r"[a-z0-9]{1,5}", ext or "", re.I) else "mp4"
    base = unicodedata.normalize("NFKC", name or "")
    base = re.sub(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩﻿]", "", base)
    base = re.sub(r'[<>:"/\\|?*]', " ", base)
    base = re.sub(r"\.{2,}", ".", base)
    base = re.sub(r"\s+", " ", base).strip().strip(".- ")[:120].strip()
    if not base or re.fullmatch(r"(?i)con|prn|aux|nul|com\d|lpt\d", base):
        base = "video"
    return f"{base}.{ext}"


def download(url: str, format_id: str, store: JobStore) -> Job:
    """Downloads one format into a private temp folder. Runs in a worker thread."""
    if not _download_slots.acquire(blocking=False):
        raise ApiError("SERVER_BUSY")
    job_id, directory = store.new_directory()
    try:
        merge = can_merge()
        selector = selector_for(format_id, merge)
        if not selector:
            raise ApiError("FORMAT_NOT_FOUND")

        info = extract(url)
        duration = info.get("duration") or 0
        if duration > settings.max_duration_seconds:
            raise ApiError("FILE_TOO_LARGE", "too long")
        available = {f["id"]: f for f in build_formats(info, merge)}
        chosen = available.get(format_id)
        if not chosen:
            raise ApiError("FORMAT_NOT_FOUND")

        options = {
            **_base_options(),
            "format": selector,
            "outtmpl": {"default": os.path.join(directory, "media.%(ext)s")},
            "max_filesize": settings.max_filesize_mb * 1024 * 1024,
            "overwrites": True,
        }
        if merge and format_id != "audio":
            options["merge_output_format"] = "mp4"
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.process_ie_result(info, download=True)
        except (DownloadError, ExtractorError) as err:
            raise ApiError(map_ytdlp_error(str(err)), "download failed") from None

        files = [f for f in os.listdir(directory) if f.startswith("media.") and not f.endswith((".part", ".ytdl"))]
        if not files:
            # yt-dlp silently skips files over max_filesize.
            raise ApiError("FILE_TOO_LARGE", "no output")
        path = os.path.join(directory, files[0])
        ext = files[0].rsplit(".", 1)[-1].lower()
        size = os.path.getsize(path)
        if size > settings.max_filesize_mb * 1024 * 1024:
            raise ApiError("FILE_TOO_LARGE")

        suffix = "" if format_id == "audio" else f" {chosen['label']}"
        job = Job(
            id=job_id,
            directory=directory,
            path=path,
            filename=sanitize_filename(f"{info.get('title') or 'video'}{suffix}", ext),
            content_type=CONTENT_TYPES.get(ext, "application/octet-stream"),
            size=size,
            expires_at=time.time() + settings.file_ttl_seconds,
        )
        store.register(job)
        return job
    except BaseException:
        store.remove(job_id)
        raise
    finally:
        _download_slots.release()
