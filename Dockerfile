FROM python:3.12-slim

# ffmpeg merges separate video + audio streams (needed for HD on most platforms).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JavaScript runtime to handle YouTube's player; Deno is its recommended one.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -U -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 10001 clipdrop && mkdir -p /tmp/clipdrop && chown clipdrop /tmp/clipdrop
USER clipdrop

ENV PYTHONUNBUFFERED=1 WORK_DIR=/tmp/clipdrop PORT=8000
EXPOSE 8000
# --proxy-headers so request.base_url is https behind Railway's proxy.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
