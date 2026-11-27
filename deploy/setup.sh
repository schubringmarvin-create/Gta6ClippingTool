#!/usr/bin/env bash
# Einrichtung auf einem frischen Ubuntu-Server (AWS, Hetzner, ...).
# Aufruf im Projektordner:  sudo bash deploy/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte mit sudo starten:  sudo bash deploy/setup.sh"; exit 1
fi

echo "=== GTA Clips – Einrichtung ==="

# 1) Auslagerungsspeicher (Swap), damit 2-GB-Server beim Schneiden nicht abstürzen
if ! swapon --show | grep -q .; then
  echo "-> Lege 2 GB Swap an ..."
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# 2) Docker installieren
if ! command -v docker >/dev/null 2>&1; then
  echo "-> Installiere Docker (dauert 1-2 Minuten) ..."
  curl -fsSL https://get.docker.com | sh >/dev/null
fi

# 3) Zugangsdaten abfragen und .env schreiben
if [ -f .env ] && [ "${1:-}" != "--neu" ]; then
  echo "-> .env existiert schon – wird behalten (neu eingeben: sudo bash deploy/setup.sh --neu)"
else
  IP="$(curl -fsS https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
  DEFAULT_DOMAIN=""
  [ -n "$IP" ] && DEFAULT_DOMAIN="$(echo "$IP" | tr '.' '-').sslip.io"
  echo
  read -r -p "Adresse der App [${DEFAULT_DOMAIN}]: " DOMAIN
  DOMAIN="${DOMAIN:-$DEFAULT_DOMAIN}"
  read -r -p "Twitch Client-ID: " TWITCH_ID
  read -r -p "Twitch Client-Secret: " TWITCH_SECRET
  read -r -p "Anthropic API-Key (optional, Enter = ohne): " ANTHROPIC
  read -r -p "Passwort für die App, nur Buchstaben+Zahlen (Enter = zufällig): " PW
  PW="$(echo "$PW" | tr -dc 'A-Za-z0-9')"
  if [ -z "$PW" ]; then PW="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16)"; fi

  cp .env.example .env
  chmod 600 .env
  setv() { # Schlüssel=Wert in .env setzen (Wert wörtlich, ohne sed-Sonderzeichen-Probleme)
    python3 - "$1" "$2" <<'PY'
import sys, pathlib
key, val = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env"); lines = p.read_text().splitlines()
lines = [f"{key}={val}" if l.startswith(key + "=") else l for l in lines]
p.write_text("\n".join(lines) + "\n")
PY
  }
  setv DOMAIN "$DOMAIN"
  setv APP_PASSWORD "$PW"
  setv TWITCH_CLIENT_ID "$TWITCH_ID"
  setv TWITCH_CLIENT_SECRET "$TWITCH_SECRET"
  setv ANTHROPIC_API_KEY "$ANTHROPIC"
fi

# 4) Starten
echo "-> Baue und starte die App (beim ersten Mal 3-5 Minuten) ..."
docker compose up -d --build

DOMAIN_NOW="$(grep '^DOMAIN=' .env | cut -d= -f2-)"
PW_NOW="$(grep '^APP_PASSWORD=' .env | cut -d= -f2-)"
echo
echo "=============================================="
echo " Fertig! Öffne auf dem Handy:"
echo "   https://${DOMAIN_NOW}"
echo " Passwort: ${PW_NOW}"
echo " (Das HTTPS-Zertifikat kann 1-2 Minuten dauern.)"
echo "=============================================="
