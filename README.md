# ⚽ TeamBot

Matrix-Bot für die wöchentliche Fußball-Teamaufstellung. Erstellt automatisch ausgeglichene Teams auf Basis von Spieler-Scores, verwaltet die Torwart-Zuweisung und berechnet Scores nach jedem Spiel neu.

---

## Features

| Feature | Details |
|---|---|
| 🗳️ **Wöchentlicher Vote** | Samstag 12:00 – Bot postet automatisch einen Poll |
| ✅ **Abstimmung** | Spieler stimmen per nativen Matrix-Poll ab |
| 🧤 **GK-Meldung** | Spieler melden sich mit `!gk` freiwillig als Torwart |
| ⚽ **Team-Vorschläge** | Mehrere Vorschläge A/B/C generieren und abstimmen |
| ⚖️ **Score-Balancing** | Teams werden nach effektivem Score optimal ausgeglichen |
| 🤖 **Interaktives Menü** | `!cmd` öffnet geführtes Poll-Menü im Admin-Raum |
| 🌐 **Web-API** | FastAPI-Endpunkte für spätere Weboberfläche vorbereitet |

---

## Score-System

Jeder Spieler hat zwei Scores (0–10, Schrittweite 0,01):

| Score | Beschreibung |
|---|---|
| `field` | Feldspieler-Stärke |
| `gk` | Torwart-Qualität (nur für GK-fähige Spieler relevant) |

**Effektiver Score** für Balancing und Zuweisung:
- `can_gk = false` → `field`
- `can_gk = true` → `0,5 × field + 0,5 × gk`

### Score-Neuberechnung nach `!result`

```
neuer_score = letzter_score × 0,50
            + Ø_letzte_5_Spiele × 0,30
            + letztes_Spiel × 0,20
```

Der **letzte berechnete Score** (nicht der Durchschnitt aller Spiele) ist die Basis. Das führt zu gleichmäßigem Konvergenzverhalten ähnlich einem ELO-System. `field` und `gk` werden getrennt berechnet – nur wenn der Spieler in der jeweiligen Rolle gespielt hat.

**Match-Score** aus Tordifferenz: `clamp(5 + tordifferenz, 0, 10)`

---

## Befehle

### Für alle Nutzer

| Befehl | Beschreibung |
|---|---|
| `!player` | Spielerliste mit Scores und Matrix-ID |
| `!match [N]` | Letzte 5 (oder N) Ergebnisse |
| `!gk` | Als Torwart für dieses Spiel melden |
| `!kein_gk` | GK-Meldung zurückziehen |
| `!team` | Neuen Team-Vorschlag generieren (A, B, C, …) |
| `!team A` | Vorschlag A aktivieren |
| `!team vote` | Alle Vorschläge zur Abstimmung stellen |
| `!help` | Alle Befehle anzeigen |

### Admin – Interaktiv (nur im Admin-Raum)

| Befehl | Beschreibung |
|---|---|
| `!cmd` | Interaktives Menü via Poll starten |

Das Menü führt durch drei Kategorien:

**👤 Spieler** – Spieler anlegen, Scores setzen, GK-Fähigkeit, deaktivieren

**⚽ Spieltag** – Teams generieren, Vorschläge, Gäste, Korrekturen, Ergebnis, Vote

**📊 Auswertung** – Spielerliste, Match-Historie, Scores

Bei Befehlen die einen Namen/Wert benötigen, wird die nächste Nachricht als Eingabe verwendet. Polls werden nach Auswahl automatisch gelöscht.

### Admin – Direkte Befehle

Name oder `@user:server` sind überall möglich.

**Spieler-Stammdaten**

| Befehl | Beschreibung |
|---|---|
| `!player add @user:server [Name] [gk]` | Spieler anlegen – ohne Name: Matrix-Anzeigename |
| `!player set Name 7.5` | Feldspieler-Score setzen (Standard) |
| `!player set Name field 7.5` | Feldspieler-Score setzen (explizit) |
| `!player set Name gk 8.0` | Torwart-Score setzen |
| `!player gk Name` | GK-Fähigkeit ein/aus (Score bleibt erhalten) |
| `!player del Name` | Spieler deaktivieren |

**Aktuelles Spiel**

| Befehl | Beschreibung |
|---|---|
| `!match guest "Name" [Score]` | Gastspieler hinzufügen (kein Score-Update) |
| `!match change Name1 [Name2]` | Spieler tauschen oder verschieben |
| `!match gk Name` | Spieler als Torwart seines Teams setzen |
| `!match switched Name` | Score-Wertung ein-/ausschalten (Toggle) |

**Ergebnis & Vote**

| Befehl | Beschreibung |
|---|---|
| `!result 3:2` | Ergebnis eintragen und Scores neu berechnen |
| `!vote` | Wöchentlichen Vote sofort starten |

---

## Wöchentlicher Ablauf

```
Samstag 12:00  →  Bot postet Poll "Kicken Morgen, 23.03.2025 um 10:00"
                   Spieler stimmen mit ✅ / ❌ ab
                   Wer Torwart spielen möchte: !gk schreiben

Sonntag 09:00  →  Bot generiert automatisch Vorschlag A
                   !team für Vorschlag B, C, …
                   !team vote → Abstimmung unter allen Vorschlägen

Sonntag 10:00  →  Meistgewählter Vorschlag wird automatisch aktiviert
                   (oder manuell: !team A / !team B)

Bei Bedarf     →  Korrekturen mit !match change / !match gk
                   Gastspieler: !match guest "Name"

Nach dem Spiel →  Admin: !result 3:2
                   Bot postet Ergebnis und aktualisiert alle Scores
```

---

## Setup

### 1. Repository klonen

```bash
git clone https://github.com/Phat-shot/Teambot.git
cd Teambot
```

### 2. Räume anlegen

Zwei Matrix-Räume erstellen – **beide ohne E2E-Verschlüsselung**:

- **Hauptraum** – für alle Spieler (Vote, Teams, Ergebnisse)
- **Admin-Raum** – privat, nur für Admins (alle Mitglieder = Admin)

### 3. Konfiguration anlegen

```bash
cp config.yml.example config.yml
nano config.yml
```

| Feld | Beschreibung |
|---|---|
| `homeserver` | URL des Matrix-Homeservers |
| `user_id` | Matrix-ID des Bot-Accounts |
| `password` | Passwort des Bot-Accounts |
| `room_id` | Raum-ID des Hauptraums |
| `admin_room_id` | Raum-ID des Admin-Raums |

Raum-IDs findest du unter: Raum → Einstellungen → Erweitert → Interne Raum-ID

### 4. Starten (Docker)

```bash
docker compose pull teambot
docker compose up -d teambot
```

### 5. Bot einladen

In beiden Räumen: `/invite @teambot:example.org`

Der Bot tritt automatisch bei. Wer im Admin-Raum ist, hat automatisch Admin-Rechte.

### Updates

```bash
docker compose pull teambot && docker compose up -d teambot
```

---

## Räume & Berechtigungen

| Raum | Wer | Was |
|---|---|---|
| Hauptraum | Alle Spieler | Vote abstimmen, `!gk`, `!team`, `!player`, `!match` lesen |
| Admin-Raum | Admins | `!cmd`, alle schreibenden Befehle, direkte Commands |
| Beide Räume | Admins | Direkte Commands funktionieren überall |

Announcements (Vote Sa 12:00, Teams So 09:00, Ergebnis) gehen immer in den **Hauptraum**.

---

## Web-API (Phase 2)

```bash
docker compose --profile api up -d
```

```
GET http://localhost:8080/players        → Alle aktiven Spieler
GET http://localhost:8080/players/1      → Einzelspieler
GET http://localhost:8080/matches/last   → Letztes Match
GET http://localhost:8080/health         → Status
```

---

## Datenbankstruktur

```
players              – Spieler, field/gk-Score, base-Scores, GK-Fähigkeit
matches              – Matchergebnisse inkl. Torwart-IDs
match_participations – Score-Protokoll pro Spieler/Match (GK-Flag)
votes                – Abstimmungsnachrichten (Matrix Event-IDs)
vote_responses       – Abstimmungs-Antworten der Spieler
gk_requests          – !gk Meldungen pro Vote
```

Beim Update wird die Datenbank automatisch migriert. Die Datei liegt unter `data/teambot.db` und wird per Docker-Volume persistiert.

---

## Entwicklung

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py          # startet interaktiven Setup-Assistenten wenn keine config.yml
python test_local.py    # Selbsttest ohne Matrix-Verbindung
```
