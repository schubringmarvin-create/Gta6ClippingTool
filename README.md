# GTA Clips – automatischer Clip-Ersteller als Handy-App

Beobachtet GTA-6-Streams auf Twitch (und YouTube), liest den Chat mit und schneidet automatisch einen
fertigen 9:16-Clip, sobald der Chat eskaliert. Du steuerst alles vom Handy: Clips ansehen, Hook anpassen,
Regeln der Kampagne prüfen, teilen/posten und Einnahmen tracken.

Läuft auf einem kleinen Server statt auf dem Laptop – der Laptop muss also nicht mehr an sein.

## Was die App kann

| Bereich | Funktion |
|---|---|
| **Live** | Wer von deiner Liste ist live, Chat-Tempo gegenüber normal (z. B. `0.6/s (normal 0.2)`), Hype-Anzeige, Knopf „Jetzt clippen“ |
| **Hype** | Löst aus, wenn der Chat x-mal schneller ist als normal; Wörter wie CLIP IT, KEKW, OMG senken die Schwelle |
| **Clip** | Holt die Stelle aus dem Twitch-VOD (Fallback: Community-Clip) bzw. YouTube-Stream, schneidet auf 9:16 (20–60 s, nach Kampagne), Hook-Satz oben |
| **Clips** | Video ansehen, Hook/Layout ändern und neu schneiden, Teilen-Knopf (öffnet direkt TikTok/Instagram/YouTube), Download, Clip aus beliebigem Link |
| **Kampagnen** | Rate pro 1.000 Aufrufe, Mindest-Aufrufe, Plattformen, Pflicht-Tags, Pflicht-Erwähnung, Regeln. Import der `kampagnen.json` vom Laptop |
| **Regel-Check** | Vor jedem Eintragen als „gepostet“: Clips erlaubt? Plattform? Länge? Pflicht-Tags/Erwähnung? Ohne Erlaubnis: Stopp |
| **Tracker** | Posts, Aufrufe, geschätzte und ausgezahlte Einnahmen, Export als CSV-Tabelle |

Gepostet wird immer von dir selbst – die App postet nichts automatisch.

**Untertitel:** standardmäßig **aus** (spart RAM). Du fügst sie am Ende selbst ein (z. B. CapCut oder
Auto-Untertitel in TikTok/Instagram). Wer einen größeren Server hat, kann sie mit `ENABLE_CAPTIONS=1` einschalten.

---

## Einrichtung (einmalig, ca. 20 Minuten)

### 1. Server mieten

Ein kleiner Linux-Server (VPS) reicht, z. B. bei **Hetzner Cloud**:

- **2 vCPU, 2–4 GB RAM, 40 GB SSD**, Standort Deutschland, Ubuntu 24.04
- ca. 4–6 € im Monat
- Beim Anlegen ein **Root-Passwort** oder SSH-Schlüssel setzen, danach die **IP-Adresse** notieren (z. B. `49.12.34.56`).

### 2. Twitch-Entwickler-App anlegen (kostenlos)

1. <https://dev.twitch.tv/console/apps> öffnen → „Register Your Application“
2. Name: beliebig, z. B. `gta-clips-deinname`
3. OAuth Redirect URL: `http://localhost`
4. Kategorie: „Application Integration“, Client-Typ: „Confidential“
5. **Client-ID** und (über „New Secret“) **Client-Secret** notieren.

### 3. Auf den Server verbinden

Am Handy z. B. mit der App **Termius**, am PC mit dem Terminal:

```bash
ssh root@49.12.34.56
```

### 4. Installieren

**Einfachster Weg (Skript fragt alles ab):**

```bash
git clone https://github.com/schubringmarvin-create/Gta6ClippingTool.git gta-clips
cd gta-clips
sudo bash deploy/setup.sh
```

Das Skript installiert Docker, legt Swap-Speicher an, fragt Twitch-Schlüssel und Passwort ab, erkennt die
Adresse automatisch und startet die App. Fertig – weiter mit Schritt 6.

**Oder von Hand:**

Diese Befehle nacheinander einfügen:

```bash
curl -fsSL https://get.docker.com | sh
git clone https://github.com/schubringmarvin-create/Gta6ClippingTool.git gta-clips
cd gta-clips
cp .env.example .env
nano .env
```

Das Repo ist privat: `git clone` fragt nach Benutzername und Passwort. Als Passwort einen
GitHub-Token nehmen (GitHub → Settings → Developer settings → Personal access tokens, Recht „Contents: Read“).

In `nano` ausfüllen (Speichern: `Strg+O`, Enter, `Strg+X`):

```
DOMAIN=49-12-34-56.sslip.io
APP_PASSWORD=ein-langes-passwort
TWITCH_CLIENT_ID=...
TWITCH_CLIENT_SECRET=...
ANTHROPIC_API_KEY=
```

- `DOMAIN`: deine Server-IP mit Bindestrichen statt Punkten + `.sslip.io` (oder eine eigene Domain)
- `ANTHROPIC_API_KEY`: optional – dann schreibt Claude Hook und Caption

Die Schlüssel stehen nur in der `.env` auf deinem Server – nie im Git.

### 5. Starten

```bash
docker compose up -d --build
```

Nach 1–3 Minuten ist die App unter `https://49-12-34-56.sslip.io` erreichbar (HTTPS-Zertifikat kommt automatisch).

### 6. Auf dem Handy installieren

- **iPhone (Safari):** Adresse öffnen → Teilen-Symbol → „Zum Home-Bildschirm“
- **Android (Chrome):** Adresse öffnen → Menü ⋮ → „App installieren“

Dann mit deinem `APP_PASSWORD` anmelden, unter **Kampagnen** die Kampagnen anlegen (oder die
`kampagnen.json` vom Laptop importieren) und unter **Streamer** die Kanäle hinzufügen.

---

## Bedienung im Alltag

Nichts zu tun – der Server läuft durchgehend. Bei einem Hype erscheint nach ca. 2–3 Minuten ein fertiger
Clip unter **Clips**. Dort: ansehen → Hook ggf. anpassen → **Teilen / Posten** → nach dem Posten
**„Regeln prüfen & als gepostet eintragen“** → später im **Tracker** die Aufrufe nachtragen.

## Wartung

```bash
cd gta-clips
git pull && docker compose up -d --build   # Update einspielen
docker compose logs -f app                 # Protokoll ansehen (Strg+C beendet)
docker compose restart app                 # neu starten
```

Clips liegen in `gta-clips/data/clips`. Gelöschte Clips in der App werden auch auf dem Server entfernt.

## Feineinstellung (in `.env`, danach `docker compose up -d`)

| Variable | Standard | Bedeutung |
|---|---|---|
| `HYPE_FACTOR` | 3.0 | Chat muss x-mal schneller sein als normal |
| `HYPE_MIN_RATE` | 1.0 | mindestens so viele Nachrichten pro Sekunde |
| `HYPE_COOLDOWN` | 120 | Sekunden Pause nach einem Treffer |
| `CLIP_BEFORE` / `CLIP_AFTER` | 35 / 10 | Sekunden vor / nach dem Hype-Moment |
| `VOD_DELAY` | 90 | Wartezeit, bis die Stelle im Twitch-VOD liegt |
| `ENABLE_CAPTIONS` | 0 | 1 = Untertitel einbrennen (braucht ca. 1–2 GB RAM mehr, danach `docker compose up -d --build`) |

## Gut zu wissen

- **Twitch-VODs:** Der Clip wird aus dem laufenden VOD geschnitten. Hat ein Streamer VODs abgeschaltet,
  nimmt die App den meistgesehenen Community-Clip aus diesem Moment – gibt es keinen, schlägt der Clip fehl.
- **YouTube** blockiert Downloads von Server-Adressen manchmal. Twitch läuft zuverlässig.
- Poste nur Clips von Streamern, die es erlauben (Kampagne mit „Clips erlaubt“), sonst drohen Copyright-Strikes.
- Die Einnahmen im Tracker sind eine Schätzung (Rate × Aufrufe, unter der Mindestzahl 0). Maßgeblich ist,
  was die Clipping-Plattform auszahlt – das trägst du unter „Ausgezahlt“ ein.

## Entwicklung

```bash
pip install -r requirements.txt pytest
DATA_DIR=./data APP_PASSWORD=test uvicorn app.main:app --reload
python -m pytest -q
```
