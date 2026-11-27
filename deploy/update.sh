#!/usr/bin/env bash
# Holt die neueste Version von GitHub und baut die App neu.
#   update.sh                -> immer prüfen (nächtlicher Lauf)
#   update.sh --if-requested -> nur, wenn in der App "Update" getippt wurde
set -uo pipefail
cd "$(dirname "$0")/.."
DATA=./data
mkdir -p "$DATA"
# git als Besitzer des Ordners ausführen (nicht als root), damit "git pull" von Hand weiter klappt
OWNER="$(stat -c %U .)"
G() { if [ "$(id -u)" = 0 ] && [ "$OWNER" != root ]; then runuser -u "$OWNER" -- git "$@"; else git "$@"; fi; }
exec 9>"$DATA/.update.lock"
flock -n 9 || exit 0   # läuft schon

if [ "${1:-}" = "--if-requested" ]; then
  [ -f "$DATA/update-request" ] || exit 0
fi
rm -f "$DATA/update-request"

status() { # status <state> <message>
  printf '{"state":"%s","message":"%s","at":%s,"version":"%s"}\n' "$1" "$2" "$(date +%s)" \
    "$(G log -1 --format='%h · %cd' --date=format:'%d.%m.%Y %H:%M' 2>/dev/null)" > "$DATA/update-status.json"
}

status running "Suche nach Updates …"
G fetch -q origin main 2>/dev/null || { status error "GitHub nicht erreichbar"; exit 1; }
if [ "$(G rev-parse HEAD)" = "$(G rev-parse origin/main)" ]; then
  status ok "Schon auf dem neuesten Stand"
  exit 0
fi
status running "Installiere Update (2–5 Minuten) …"
if G pull -q --ff-only origin main && docker compose up -d --build >"$DATA/update.log" 2>&1; then
  status ok "Update installiert"
else
  status error "Update fehlgeschlagen – siehe data/update.log"
  exit 1
fi
