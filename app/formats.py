"""Turns yt-dlp's format table into the short list users choose from."""

import re
from typing import Any

MAX_HEIGHT = 2160
MAX_VIDEO_OPTIONS = 6


def _has_video(f: dict) -> bool:
    return bool(f.get("vcodec")) and f.get("vcodec") != "none"


def _has_audio(f: dict) -> bool:
    return bool(f.get("acodec")) and f.get("acodec") != "none"


def _size(f: dict | None) -> int | None:
    if not f:
        return None
    return f.get("filesize") or f.get("filesize_approx") or None


def _best(formats: list[dict], ext: str) -> dict | None:
    """Prefer H.264, then the given container (both play everywhere), then higher bitrate."""
    if not formats:
        return None
    return sorted(
        formats,
        key=lambda f: (str(f.get("vcodec") or "").startswith(("avc1", "h264")), f.get("ext") == ext, f.get("tbr") or 0),
        reverse=True,
    )[0]


def _quality(height: int) -> str:
    if height >= 2160:
        return "4K Video"
    if height >= 1440:
        return "QHD Video"
    if height >= 720:
        return "HD Video"
    return "SD Video"


def build_formats(info: dict[str, Any], can_merge: bool = True) -> list[dict[str, Any]]:
    """One option per resolution, plus audio only. Only formats the source returned are listed."""
    if info.get("is_live"):
        return []
    usable = [f for f in info.get("formats") or [] if f.get("format_id") and not f.get("has_drm")]
    videos = [f for f in usable if _has_video(f) and f.get("height") and f["height"] <= MAX_HEIGHT]
    audios = [f for f in usable if _has_audio(f) and not _has_video(f)]
    best_audio = _best(audios, "m4a")

    # Without an audio stream to merge, only formats that already contain audio work.
    candidates = videos if (can_merge and best_audio) else [f for f in videos if _has_audio(f)]
    heights = sorted({f["height"] for f in candidates}, reverse=True)[:MAX_VIDEO_OPTIONS]

    result: list[dict[str, Any]] = []
    for height in heights:
        at_height = [f for f in candidates if f["height"] == height]
        video = _best(at_height, "mp4")
        progressive = _best([f for f in at_height if _has_audio(f)], "mp4")
        merged = bool(can_merge and best_audio and not _has_audio(video))
        chosen = video if merged else (progressive or video)
        if merged:
            vs, as_ = _size(video), _size(best_audio)
            size = vs + as_ if vs and as_ else None
        else:
            size = _size(chosen)
        # Label by the short side so a vertical 720x1280 Reel reads "720p", like the apps show it.
        width = chosen.get("width")
        short = min(width, height) if isinstance(width, int) and width > 0 else height
        result.append(
            {
                "id": f"v{height}",
                "label": f"{short}p",
                "description": _quality(short),
                "container": "MP4" if merged or chosen.get("ext") == "mp4" else str(chosen.get("ext") or "mp4").upper(),
                "sizeBytes": size,
                "width": chosen.get("width"),
                "height": height,
                "hasAudio": True,
            }
        )

    if best_audio:
        result.append(
            {
                "id": "audio",
                "label": "Audio only",
                "description": "M4A audio" if best_audio.get("ext") == "m4a" else "Audio track",
                "container": str(best_audio.get("ext") or "m4a").upper(),
                "sizeBytes": _size(best_audio),
                "hasAudio": True,
            }
        )
    return result


_VIDEO_ID = re.compile(r"^v(\d{3,4})$")


def selector_for(format_id: str, can_merge: bool = True) -> str | None:
    """yt-dlp format selector for one of our IDs. IDs come from build_formats, never free text."""
    if format_id == "audio":
        return "ba[ext=m4a]/ba"
    match = _VIDEO_ID.match(format_id or "")
    if not match:
        return None
    h = int(match.group(1))
    if can_merge:
        # Prefer H.264 (plays on every phone/TV/editor), then any MP4, then whatever exists.
        return (
            # Platforms label H.264 as "avc1" (YouTube) or "h264" (TikTok).
            f"bv*[height={h}][vcodec~='^(avc1|h264)']+ba[ext=m4a]/b[height={h}][vcodec~='^(avc1|h264)']"
            f"/bv*[height={h}][ext=mp4]+ba[ext=m4a]"
            f"/bv*[height={h}]+ba/b[height={h}]"
        )
    return f"b[height={h}][vcodec~='^(avc1|h264)'][acodec!=none]/b[height={h}][vcodec!=none][acodec!=none]"


def video_info(info: dict[str, Any], platform: str, platform_name: str, page_url: str) -> dict[str, Any]:
    title = " ".join(str(info.get("title") or info.get("fulltitle") or "").split())
    author = str(info.get("uploader") or info.get("channel") or info.get("creator") or "").strip()
    # TikTok returns a placeholder title ("TikTok video #123…") and puts the caption in the description.
    if not title or re.fullmatch(r"(TikTok )?video #?\d+", title, re.I):
        caption = " ".join(str(info.get("description") or "").split())
        title = caption or (f"{platform_name} video by {author}" if author else title)
    thumbs = [info.get("thumbnail")] + [t.get("url") for t in reversed(info.get("thumbnails") or [])]
    duration = info.get("duration")
    return {
        "source": platform,
        "sourceName": platform_name,
        "title": title[:300] or f"{platform_name} video",
        "pageUrl": page_url,
        "author": author[:120] or None,
        "durationSeconds": duration if isinstance(duration, (int, float)) and duration > 0 else None,
        # The Next.js app filters these against its thumbnail host allowlist.
        "thumbnailCandidates": [t for t in thumbs if isinstance(t, str) and t.startswith("https://")][:8],
    }
