FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt requirements-captions.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Untertitel-Baustein (nur geladen, wenn gebraucht). Schlägt die Installation fehl, läuft die App trotzdem.
RUN pip install --no-cache-dir -r requirements-captions.txt || echo "WARNUNG: Untertitel nicht installiert"

COPY app ./app
ENV DATA_DIR=/data PYTHONUNBUFFERED=1 HF_HOME=/data/models TZ=Europe/Berlin
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
