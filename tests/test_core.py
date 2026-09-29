import os
import time

import pytest

from app.errors import map_ytdlp_error
from app.extractor import sanitize_filename
from app.formats import build_formats, selector_for
from app.jobs import Job, JobStore
from app.ratelimit import RateLimiter
from app.validation import validate_url


@pytest.mark.parametrize(
    "url,platform",
    [
        ("https://www.youtube.com/watch?v=aqz-KE-bpKQ", "youtube"),
        ("https://youtu.be/aqz-KE-bpKQ", "youtube"),
        ("https://www.tiktok.com/@a/video/123456789", "tiktok"),
        ("https://vm.tiktok.com/ZMabc/", "tiktok"),
        ("https://www.instagram.com/reel/abc/", "instagram"),
        ("https://fb.watch/abc/", "facebook"),
    ],
)
def test_validate_accepts_platform_links(url, platform):
    result = validate_url(url)
    assert result is not None and result[1] == platform and result[0].startswith("https://")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://127.0.0.1/video",
        "https://[::1]/video",
        "https://localhost/video",
        "https://example.com/video.mp4",
        "https://youtube.com.evil.example/watch?v=x",
        "https://notyoutube.com/watch?v=x",
        # Removed platforms
        "https://x.com/u/status/1",
        "https://vimeo.com/1084537",
        "https://www.reddit.com/r/a/comments/b/c/",
        "https://user:pw@youtube.com/watch?v=x",
        "https://youtube.com:8443/watch?v=x",
        "https://youtube.com/watch?v=x\n--exec",
        "https://" + "a" * 3000 + ".com",
        None,
    ],
)
def test_validate_rejects_everything_else(url):
    assert validate_url(url) is None


INFO = {
    "title": "Clip",
    "formats": [
        {"format_id": "18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "height": 360, "width": 640, "filesize": 5_000_000},
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 1080, "filesize": 40_000_000, "tbr": 4000},
        {"format_id": "248", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 1080, "filesize": 35_000_000, "tbr": 3000},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "filesize": 3_000_000, "tbr": 128},
        {"format_id": "drm", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "height": 2160, "has_drm": True},
        {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none"},
    ],
}


def test_build_formats_with_merge():
    formats = build_formats(INFO, can_merge=True)
    assert [f["id"] for f in formats] == ["v1080", "v360", "audio"]
    assert formats[0] == {
        "id": "v1080", "label": "1080p", "description": "HD Video", "container": "MP4",
        "sizeBytes": 43_000_000, "width": None, "height": 1080, "hasAudio": True,
    }
    assert formats[1]["sizeBytes"] == 5_000_000
    assert formats[2]["container"] == "M4A"


def test_build_formats_without_ffmpeg_only_offers_progressive():
    assert [f["id"] for f in build_formats(INFO, can_merge=False)] == ["v360", "audio"]


def test_build_formats_live_and_empty():
    assert build_formats({"is_live": True, "formats": INFO["formats"]}) == []
    assert build_formats({}) == []


def test_selector_only_for_known_ids():
    assert selector_for("audio") == "ba[ext=m4a]/ba"
    assert "height=720" in selector_for("v720")
    for bad in ["best", "v72", "v1080;rm -rf /", "../x", "", "bv*+ba"]:
        assert selector_for(bad) is None


@pytest.mark.parametrize(
    "message,code",
    [
        ("ERROR: [youtube] x: Sign in to confirm you're not a bot", "PROVIDER_UNAVAILABLE"),
        ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "PRIVATE_CONTENT"),
        ("ERROR: [instagram] x: login required", "PRIVATE_CONTENT"),
        ("ERROR: [youtube] x: Video unavailable", "NOT_FOUND"),
        ("ERROR: This video is DRM protected", "DOWNLOADS_UNAVAILABLE"),
        ("ERROR: Unsupported URL: https://x", "UNSUPPORTED_URL"),
        ("ERROR: [vimeo] 1: The web client only works when logged-in. Use --cookies", "PROVIDER_UNAVAILABLE"),
        ("ERROR: [TikTok] 7107: Your IP address is blocked from accessing this post", "REGION_BLOCKED"),
        ("something odd", "PROVIDER_UNAVAILABLE"),
    ],
)
def test_error_mapping(message, code):
    assert map_ytdlp_error(message) == code


def test_sanitize_filename():
    assert sanitize_filename("My Film: part/1", "mp4") == "My Film part 1.mp4"
    assert "/" not in sanitize_filename("../../etc/passwd", "mp4")
    assert sanitize_filename("", "exe;rm") == "video.mp4"
    assert sanitize_filename("CON", "m4a") == "video.m4a"
    assert "‮" not in sanitize_filename("a‮gpj", "mp4")


def test_job_store_expiry_and_ids(tmp_path):
    store = JobStore(str(tmp_path), ttl_seconds=60)
    job_id, directory = store.new_directory()
    path = os.path.join(directory, "media.mp4")
    open(path, "wb").write(b"x")
    store.register(Job(job_id, directory, path, "a.mp4", "video/mp4", 1, time.time() + 60))
    assert store.get(job_id) is not None
    assert store.get("../" + job_id) is None
    assert store.get("z" * 32) is None
    assert store.get(job_id, now=time.time() + 120) is None
    assert not os.path.exists(directory)


def test_job_store_sweeps_orphans(tmp_path):
    store = JobStore(str(tmp_path), ttl_seconds=1)
    _, directory = store.new_directory()
    os.utime(directory, (0, 0))
    assert store.sweep() == 1
    assert not os.path.exists(directory)


def test_rate_limiter():
    limiter = RateLimiter(2, 60)
    assert limiter.check("a", now=0)[0]
    assert limiter.check("a", now=0)[0]
    allowed, retry = limiter.check("a", now=10)
    assert not allowed and retry == 50
    assert limiter.check("b", now=10)[0]
    assert limiter.check("a", now=61)[0]


def test_vertical_video_labelled_by_short_side():
    info = {"formats": [{"format_id": "1", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "height": 1280, "width": 720}]}
    (fmt,) = build_formats(info, can_merge=False)
    assert fmt["id"] == "v1280" and fmt["label"] == "720p" and fmt["description"] == "HD Video"


def test_selector_prefers_h264():
    sel = selector_for("v720")
    assert sel.index("avc1|h264") < sel.index("ext=mp4]")


def test_tiktok_h264_preferred_over_hevc():
    info = {"formats": [
        {"format_id": "hevc", "ext": "mp4", "vcodec": "h265", "acodec": "aac", "height": 1024, "width": 576, "tbr": 900},
        {"format_id": "h264", "ext": "mp4", "vcodec": "h264", "acodec": "aac", "height": 1024, "width": 576, "tbr": 700, "filesize": 123},
    ]}
    (fmt,) = build_formats(info, can_merge=False)
    assert fmt["sizeBytes"] == 123  # the H.264 file was chosen
    assert "h264" in selector_for("v1024", can_merge=False)


def test_tiktok_caption_used_as_title():
    from app.formats import video_info
    info = {"title": "TikTok video #7047596209028074758", "description": "Why the sky is blue #science"}
    assert video_info(info, "tiktok", "TikTok", "https://x")["title"] == "Why the sky is blue #science"
    assert video_info({"title": "Real title"}, "tiktok", "TikTok", "https://x")["title"] == "Real title"
    no_caption = {"title": "TikTok video #7047596209028074758", "uploader": "hankgreen1"}
    assert video_info(no_caption, "tiktok", "TikTok", "https://x")["title"] == "TikTok video by hankgreen1"


REEL = {"formats": [
    {"format_id": "sd", "ext": "mp4", "vcodec": None, "acodec": None, "protocol": "https"},
    {"format_id": "hd", "ext": "mp4", "vcodec": None, "acodec": None, "protocol": "https"},
    {"format_id": "139v", "ext": "mp4", "vcodec": "vp09", "acodec": "none", "height": 636, "width": 360},
]}


def test_facebook_reel_unlabelled_files_are_offered():
    formats = build_formats(REEL, can_merge=True)
    assert [(f["id"], f["label"]) for f in formats] == [("p_hd", "HD"), ("p_sd", "SD")]
    assert selector_for("p_hd") == "hd"


def test_unlabelled_files_not_offered_when_real_resolutions_exist():
    assert [f["id"] for f in build_formats(INFO, can_merge=True)] == ["v1080", "v360", "audio"]


@pytest.mark.parametrize("bad", ["p_", "p_hd/best", "p_hd+ba", "p_a[height>1]", "p_" + "a" * 30])
def test_passthrough_ids_reject_selector_syntax(bad):
    assert selector_for(bad) is None


def test_facebook_stats_prefix_removed_from_title():
    from app.formats import video_info
    info = {"title": "9.8K views · 342 reactions | When your trying to help"}
    assert video_info(info, "facebook", "Facebook", "https://x")["title"] == "When your trying to help"
    assert video_info({"title": "Plain | title"}, "facebook", "Facebook", "https://x")["title"] == "Plain | title"
