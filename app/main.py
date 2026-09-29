"""ClipDrop downloader API.

POST /analyze   {url}            -> video details + available formats   (requires x-api-key)
POST /download  {url, formatId}  -> prepares the file, returns a link   (requires x-api-key)
GET  /files/{id}                 -> the prepared file (unguessable ID, expires automatically)
GET  /health
"""

import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote

import yt_dlp
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import extractor
from .config import settings
from .errors import MESSAGES, STATUS, ApiError
from .jobs import JobStore
from .ratelimit import RateLimiter
from .validation import MAX_URL_LENGTH, validate_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("clipdrop")

store = JobStore(settings.work_dir, settings.file_ttl_seconds)
limiter = RateLimiter(settings.rate_limit_per_minute, 60)
MAX_BODY_BYTES = 4096


async def _sweeper() -> None:
    while True:
        await asyncio.sleep(60)
        with suppress(Exception):
            removed = await asyncio.to_thread(store.sweep)
            if removed:
                log.info("cleaned up %d expired download(s)", removed)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.purge_all()  # nothing survives a restart
    if not settings.api_key:
        log.warning("API_KEY is not set: /analyze and /download are open. Set it in production.")
    task = asyncio.create_task(_sweeper())
    yield
    task.cancel()


app = FastAPI(title="ClipDrop downloader API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_methods=["GET"],
        allow_headers=[],
        expose_headers=["content-disposition", "content-length"],
    )


def error_response(code: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        {"success": False, "code": code, "message": MESSAGES[code]},
        status_code=STATUS[code],
        headers={"cache-control": "no-store", **(headers or {})},
    )


@app.exception_handler(ApiError)
async def _api_error(_: Request, err: ApiError):
    return error_response(err.code)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, err: RequestValidationError):
    url_problem = any(e.get("loc", [None, None])[-1] == "url" for e in err.errors())
    return error_response("INVALID_URL" if url_problem else "BAD_REQUEST")


@app.exception_handler(Exception)
async def _unexpected(_: Request, err: Exception):
    log.exception("unexpected error: %s", type(err).__name__)
    return error_response("INTERNAL_ERROR")


@app.middleware("http")
async def _limit_body_and_headers(request: Request, call_next):
    if request.method == "POST":
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            return error_response("BAD_REQUEST")
        if declared > MAX_BODY_BYTES:
            return error_response("BAD_REQUEST")
    response = await call_next(request)
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["referrer-policy"] = "no-referrer"
    return response


def _authorize_and_limit(request: Request, api_key: str | None, client_ip: str | None) -> None:
    if settings.api_key and not (api_key and secrets.compare_digest(api_key, settings.api_key)):
        raise ApiError("UNAUTHORIZED")
    # The Next.js server forwards the visitor's IP; trust it only from an authenticated caller.
    key = (client_ip if settings.api_key and client_ip else None) or (request.client.host if request.client else "unknown")
    allowed, retry_after = limiter.check(key[:64])
    if not allowed:
        raise _RateLimited(retry_after)


class _RateLimited(ApiError):
    def __init__(self, retry_after: int):
        super().__init__("RATE_LIMITED")
        self.retry_after = retry_after


@app.exception_handler(_RateLimited)
async def _rate_limited(_: Request, err: _RateLimited):
    return error_response("RATE_LIMITED", {"retry-after": str(err.retry_after)})


class AnalyzeBody(BaseModel):
    url: str = Field(min_length=1, max_length=MAX_URL_LENGTH)


class DownloadBody(BaseModel):
    url: str = Field(min_length=1, max_length=MAX_URL_LENGTH)
    formatId: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9_.-]+$")


def _checked(url: str) -> tuple[str, str]:
    result = validate_url(url)
    if not result:
        raise ApiError("UNSUPPORTED_URL")
    return result


async def _in_thread(fn, *args, timeout: int):
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout)
    except asyncio.TimeoutError:
        raise ApiError("PROVIDER_UNAVAILABLE", "timed out") from None


@app.get("/health")
async def health():
    return {"ok": True, "ytdlp": yt_dlp.version.__version__, "ffmpeg": extractor.can_merge()}


@app.post("/analyze")
async def analyze(
    body: AnalyzeBody,
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    x_client_ip: Annotated[str | None, Header()] = None,
):
    _authorize_and_limit(request, x_api_key, x_client_ip)
    url, platform = _checked(body.url)
    result = await _in_thread(extractor.analyze, url, platform, timeout=settings.analyze_timeout_seconds)
    return JSONResponse(result, headers={"cache-control": "no-store"})


@app.post("/download")
async def download(
    body: DownloadBody,
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    x_client_ip: Annotated[str | None, Header()] = None,
):
    _authorize_and_limit(request, x_api_key, x_client_ip)
    url, _ = _checked(body.url)
    job = await _in_thread(extractor.download, url, body.formatId, store, timeout=settings.download_timeout_seconds)
    base = settings.public_base_url or str(request.base_url).rstrip("/")
    return JSONResponse(
        {
            "success": True,
            "download": {
                "downloadUrl": f"{base}/files/{job.id}",
                "filename": job.filename,
                "expiresAt": datetime.fromtimestamp(job.expires_at, timezone.utc).isoformat(),
                "sizeBytes": job.size,
            },
        },
        headers={"cache-control": "no-store"},
    )


@app.get("/files/{job_id}")
async def get_file(job_id: str):
    job = store.get(job_id)
    if not job:
        return error_response("DOWNLOAD_EXPIRED")
    ascii_name = job.filename.encode("ascii", "ignore").decode().replace('"', "") or "video"
    return FileResponse(
        job.path,
        media_type=job.content_type,
        headers={
            "content-disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(job.filename)}",
            "cache-control": "private, no-store",
            "x-expires-in": str(max(0, int(job.expires_at - time.time()))),
        },
    )
