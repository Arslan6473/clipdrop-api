"""Public error codes. Mirrors lib/providers/errors.ts in the Next.js app."""

import re

MESSAGES: dict[str, str] = {
    "INVALID_URL": "Please enter a valid supported video URL.",
    "UNSUPPORTED_URL": "We couldn't recognize this video link. Check the URL and try again.",
    "PRIVATE_CONTENT": "This video appears to require authentication. Private or login-protected videos aren't supported.",
    "NOT_FOUND": "We couldn't find this video. It may have been removed or the link may be incomplete.",
    "DOWNLOADS_UNAVAILABLE": "Downloads aren't currently available for this source.",
    "FORMAT_NOT_FOUND": "That format is no longer available. Analyze the video again to refresh the options.",
    "PROVIDER_UNAVAILABLE": "This platform is temporarily unavailable. Please try again later.",
    "REGION_BLOCKED": "This video isn't available in our download server's region.",
    "FILE_TOO_LARGE": "This video is too large to download here. Try a lower quality.",
    "SERVER_BUSY": "We're preparing a lot of downloads right now. Please try again in a minute.",
    "DOWNLOAD_EXPIRED": "This download link has expired. Choose the format again to prepare a new one.",
    "RATE_LIMITED": "You're going a little fast. Please wait a moment and try again.",
    "UNAUTHORIZED": "Not allowed.",
    "BAD_REQUEST": "Something about that request wasn't right. Please try again.",
    "INTERNAL_ERROR": "Something went wrong on our side. Please try again.",
}

STATUS: dict[str, int] = {
    "INVALID_URL": 400,
    "UNSUPPORTED_URL": 422,
    "PRIVATE_CONTENT": 403,
    "NOT_FOUND": 404,
    "DOWNLOADS_UNAVAILABLE": 422,
    "FORMAT_NOT_FOUND": 404,
    "PROVIDER_UNAVAILABLE": 503,
    "REGION_BLOCKED": 451,
    "FILE_TOO_LARGE": 413,
    "SERVER_BUSY": 503,
    "DOWNLOAD_EXPIRED": 410,
    "RATE_LIMITED": 429,
    "UNAUTHORIZED": 401,
    "BAD_REQUEST": 400,
    "INTERNAL_ERROR": 500,
}


class ApiError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code if code in MESSAGES else "INTERNAL_ERROR"


_RULES: list[tuple[str, str]] = [
    # Order matters: bot checks mention "sign in" but aren't about private content.
    (r"confirm you.?re not a bot|rate.?limit|too many requests|http error 429", "PROVIDER_UNAVAILABLE"),
    # yt-dlp can't use a platform's public client (e.g. Vimeo now needs an account): not the video's fault.
    (r"only works when logged.in", "PROVIDER_UNAVAILABLE"),
    (r"ip address is blocked|not available in your (country|region)|geo.?restrict", "REGION_BLOCKED"),
    (r"\bdrm\b", "DOWNLOADS_UNAVAILABLE"),
    (r"max-filesize|larger than max", "FILE_TOO_LARGE"),
    (r"private|login|log in|sign in|authentication|members.only|age.restricted|inappropriate|cookies", "PRIVATE_CONTENT"),
    (r"unsupported url|no suitable extractor", "UNSUPPORTED_URL"),
    (r"requested format is not available", "FORMAT_NOT_FOUND"),
    (r"unavailable|not found|404|removed|does not exist|no video|deleted", "NOT_FOUND"),
]


def map_ytdlp_error(message: str) -> str:
    """Maps yt-dlp's error text onto a safe public code. Raw text never reaches users."""
    text = (message or "").lower()
    for pattern, code in _RULES:
        if re.search(pattern, text):
            return code
    return "PROVIDER_UNAVAILABLE"
