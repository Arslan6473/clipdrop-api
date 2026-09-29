"""Settings, all read from environment variables (Railway → Variables)."""

import os
import tempfile
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Shared secret the Next.js server sends in `x-api-key`. Required in production.
    api_key: str = field(default_factory=lambda: os.environ.get("API_KEY", ""))
    # Public base URL of this service, used to build file links, e.g. https://clipdrop-api.up.railway.app
    public_base_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_BASE_URL", "").rstrip("/"))
    # Browser origins allowed to fetch files (comma separated), e.g. https://clipdrop.example
    allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip())
    )
    work_dir: str = field(default_factory=lambda: os.environ.get("WORK_DIR", os.path.join(tempfile.gettempdir(), "clipdrop")))
    file_ttl_seconds: int = field(default_factory=lambda: _int("FILE_TTL_SECONDS", 15 * 60))
    max_filesize_mb: int = field(default_factory=lambda: _int("MAX_FILESIZE_MB", 1024))
    max_duration_seconds: int = field(default_factory=lambda: _int("MAX_DURATION_SECONDS", 3 * 60 * 60))
    max_concurrent_downloads: int = field(default_factory=lambda: _int("MAX_CONCURRENT_DOWNLOADS", 3))
    analyze_timeout_seconds: int = field(default_factory=lambda: _int("ANALYZE_TIMEOUT_SECONDS", 45))
    download_timeout_seconds: int = field(default_factory=lambda: _int("DOWNLOAD_TIMEOUT_SECONDS", 240))
    rate_limit_per_minute: int = field(default_factory=lambda: _int("RATE_LIMIT_PER_MINUTE", 60))


settings = Settings()
