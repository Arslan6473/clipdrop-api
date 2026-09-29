# ClipDrop downloader API

A small FastAPI service that uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) and ffmpeg to list and prepare downloads of **public** videos from YouTube, TikTok, Instagram and Facebook. The Next.js site calls it server-side; browsers download prepared files from it directly.

- No cookies, logins or credentials, so private and members-only content is never reachable.
- DRM formats are skipped, and yt-dlp's generic extractor is disabled, so it only contacts known platforms.
- Prepared files live in a private temp folder and are deleted after `FILE_TTL_SECONDS` (default 15 min), and all of them on restart.

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/analyze` `{url}` | `x-api-key` | Video details and available formats |
| POST | `/download` `{url, formatId}` | `x-api-key` | Prepares the file and returns `downloadUrl` |
| GET | `/files/{id}` | unguessable ID | The prepared file (`Content-Disposition: attachment`) |
| GET | `/health` | none | yt-dlp version and ffmpeg availability |

## Run locally

```bash
cd downloader-api
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
API_KEY=dev PUBLIC_BASE_URL=http://127.0.0.1:8765 .venv/bin/uvicorn app.main:app --port 8765
.venv/bin/python -m pytest
```

Then run the Next.js app with `DOWNLOADER_API_URL=http://127.0.0.1:8765 DOWNLOADER_API_KEY=dev`.

## Deploy on Railway

1. Create a new service from this repo and set **Root Directory** to `downloader-api`. Railway picks up `Dockerfile` and `railway.json`, including the `/health` check.
2. Under **Settings → Networking**, generate a public domain.
3. Add these variables:
   - `API_KEY`: a long random string (e.g. `openssl rand -hex 32`)
   - `PUBLIC_BASE_URL`: the Railway domain, e.g. `https://clipdrop-api.up.railway.app`
   - `ALLOWED_ORIGINS`: your site, e.g. `https://clipdrop.example`
4. In the Next.js project (e.g. on Vercel), set `DOWNLOADER_API_URL` to the Railway URL and `DOWNLOADER_API_KEY` to the same key, then redeploy it. The site's copy switches to the "downloads available" wording at build time.

Optional limits: `MAX_FILESIZE_MB` (1024), `MAX_DURATION_SECONDS` (10800), `MAX_CONCURRENT_DOWNLOADS` (3), `DOWNLOAD_TIMEOUT_SECONDS` (240), `RATE_LIMIT_PER_MINUTE` (60).

## Keep it working

- **Redeploy every week or two.** Each build installs the latest yt-dlp, and platforms change often.
- **YouTube may block cloud IPs** ("confirm you're not a bot"). The site then still shows video details, and download options come back once YouTube allows it again.
- The Next.js download request waits while the file is prepared. Keep `DOWNLOAD_TIMEOUT_SECONDS` below your Next.js host's function limit (300 s on Vercel by default). Very long 4K videos may not finish in time; lower resolutions will.
- Railway's disk is temporary and bills for bandwidth. `MAX_FILESIZE_MB` and `MAX_CONCURRENT_DOWNLOADS` keep costs predictable.
