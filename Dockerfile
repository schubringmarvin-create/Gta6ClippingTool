FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt requirements-captions.txt ./
ARG ENABLE_CAPTIONS=0
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$ENABLE_CAPTIONS" = "1" ]; then pip install --no-cache-dir -r requirements-captions.txt; fi

COPY app ./app
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
