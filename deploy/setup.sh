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

# 3) .env anlegen – ohne Fragen. Twitch-Schlüssel trägst du danach in der App ein.
if [ -f .env ]; then
  echo "-> .env existiert schon – wird behalten"
else
  IP="$(curl -fsS https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
  if [ -z "$IP" ]; then IP="$(curl -fsS https://api.ipify.org 2>/dev/null | tr -d '[:space:]' || true)"; fi
  if [ -z "$IP" ]; then echo "Konnte die öffentliche IP nicht ermitteln."; exit 1; fi
  DOMAIN="$(echo "$IP" | tr '.' '-').sslip.io"
  # gut lesbares Passwort (ohne l, 1, I, O, 0)
  PW="$(python3 -c "import secrets; print(''.join(secrets.choice('abcdefghjkmnpqrstuvwxyz23456789') for _ in range(10)))")"
  cp .env.example .env
  chmod 600 .env
  sed -i "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|; s|^APP_PASSWORD=.*|APP_PASSWORD=${PW}|" .env
fi

# 4) Automatische Updates: App-Knopf (minütlich geprüft) + jede Nacht um 4:30
REPO_DIR="$(pwd)"
chmod +x deploy/update.sh
cat > /etc/cron.d/gta-clips <<CRON
* * * * * root ${REPO_DIR}/deploy/update.sh --if-requested >/dev/null 2>&1
30 4 * * * root ${REPO_DIR}/deploy/update.sh >/dev/null 2>&1
CRON
chmod 644 /etc/cron.d/gta-clips

# 5) Starten
echo "-> Baue und starte die App (beim ersten Mal 3-5 Minuten) ..."
docker compose up -d --build
printf '{"state":"ok","message":"Installiert","at":%s,"version":"%s"}\n' "$(date +%s)" "$(git log -1 --format='%h · %cd' --date=format:'%d.%m.%Y %H:%M')" > data/update-status.json

DOMAIN_NOW="$(grep '^DOMAIN=' .env | cut -d= -f2-)"
PW_NOW="$(grep '^APP_PASSWORD=' .env | cut -d= -f2-)"
echo
echo "=============================================="
echo " Fertig! Öffne auf dem Handy:"
echo "   https://${DOMAIN_NOW}"
echo " Passwort: ${PW_NOW}"
echo " (Das HTTPS-Zertifikat kann 1-2 Minuten dauern.)"
echo " Danach in der App: Streamer -> Einstellungen -> Twitch-Schlüssel eintragen."
echo "=============================================="
