# ⚽ TeamBot

Matrix-Bot für die wöchentliche Fußball-Teamaufstellung. Erstellt automatisch ausgeglichene Teams auf Basis von Spieler-Scores, verwaltet die Torwart-Zuweisung und berechnet Scores nach jedem Spiel neu.

Zwei Räume: ein **Hauptraum** für alle Spieler (Vote, Ankündigungen) und ein **Admin-Raum** für die Teamverwaltung.

---

## Features

| Feature | Details |
|---|---|
| 🗳️ **Wöchentlicher Vote** | Samstag 12:00 – Bot postet automatisch einen Poll |
| ✅ **Abstimmung** | Spieler stimmen per nativen Matrix-Poll (auch via WA-Bridge) ab |
| 🥅 **GK-Meldung** | Mit 🥅-Reaktion auf den Vote-Poll als Torwart melden |
| 👤 **Gäste** | Mit 1️⃣–9️⃣-Reaktion auf den Vote-Poll Gäste hinzufügen |
| 🔃 **Team-Tausch** | Mit 🔃-Reaktion ins andere Team wechseln |
| ⚽ **Team-Vorschläge** | Mehrere Vorschläge A/B/C generieren und per Poll abstimmen |
| ⚖️ **Score-Balancing** | Teams werden nach effektivem Score optimal ausgeglichen |
| 🛠️ **Admin-Team-Poll** | Nach `!team` erscheint Poll in Admin-Gruppe zur interaktiven Bearbeitung |
| 🤖 **Interaktives Menü** | `!cmd` öffnet geführtes Poll-Menü im Admin-Raum |

---

## Räume & Berechtigungen

| Raum | Wer | Was |
|---|---|---|
| **Hauptraum** | Alle Spieler | Vote abstimmen, Reaktionen, Ankündigungen empfangen |
| **Admin-Raum** | Admins | Alle Befehle, Team-Poll, interaktives Menü |

Im Hauptraum gibt es keine Befehle – alles läuft über Reaktionen auf den Vote-Poll.

---

## Spieler-Aktionen im Hauptraum

Alle Abstimmungs-Aktionen erfolgen durch **Reaktionen auf den Vote-Poll**:

| Reaktion | Aktion |
|---|---|
| ✅ | Zusage – ich spiele mit |
| ❌ | Absage |
| 🥅 | Als Torwart melden (auch ohne GK-Flag) |
| 1️⃣–9️⃣ | N Gäste hinzufügen (z.B. 2️⃣ → „[User]s Gast 1", „[User]s Gast 2") |

Wer zum ersten Mal ✅ klickt und noch nicht registriert ist, wird automatisch angelegt (Skill 5, kein GK) und erhält eine Willkommensnachricht.

**Reaktion auf die Team-Ankündigung:**

| Reaktion | Aktion |
|---|---|
| 🔃 | Ins andere Team wechseln – automatischer Tausch mit ähnlich bewertetem Gegenspieler |

---

## Admin-Team-Poll

Nach jedem `!team`-Aufruf postet der Bot automatisch einen Poll in den **Admin-Raum**. Der Poll listet alle Spieler mit Team-Zugehörigkeit (🟡 / 🌈) als Antworten.

**Ablauf:**
1. Spieler im Poll auswählen (Multi-Select möglich)
2. Mit Emoji auf den Poll reagieren:

| Reaktion | Aktion |
|---|---|
| 🔃 | Selektierte Spieler ins andere Team wechseln (keine Neuberechnung) |
| 🥅 | Selektierte Spieler als Torwart setzen (alter TW rückt ins Feld) |
| 1️⃣–9️⃣ | N Gäste hinzufügen, fair auf beide Teams verteilt |
| 📣 | Aktuelles Team in Hauptgruppe ankündigen |

Nach jeder Aktion: alter Poll wird gelöscht, neuer Poll wird gepostet. Der Poll bleibt sichtbar bis `!result` eingegeben wird.

---

## Admin-Befehle (nur im Admin-Raum)

### Team & Vote

| Befehl | Beschreibung |
|---|---|
| `!team` | Neuen Team-Vorschlag generieren (A, B, C, …) |
| `!team A` | Vorschlag A aktivieren |
| `!team vote` | Alle Vorschläge zur Abstimmung stellen |
| `!vote` | Wöchentlichen Vote sofort starten |
| `!result 3:2` | Ergebnis eintragen und Scores neu berechnen |
| `!help` | Alle Befehle anzeigen |

### Spieler-Stammdaten

| Befehl | Beschreibung |
|---|---|
| `!player` | Spielerliste mit Scores |
| `!player add @user:server [Name] [gk]` | Spieler anlegen |
| `!player set Name 7.5` | Feldspieler-Score setzen |
| `!player set Name field 7.5` | Feldspieler-Score setzen (explizit) |
| `!player set Name gk 8.0` | Torwart-Score setzen |
| `!player gk Name` | GK-Fähigkeit ein/aus (Score bleibt) |
| `!player del Name` | Spieler deaktivieren |

### Spieltag-Korrekturen

| Befehl | Beschreibung |
|---|---|
| `!match [N]` | Letzte 5 (oder N) Ergebnisse |
| `!match change Name1 [Name2]` | Spieler tauschen oder verschieben |
| `!match gk Name` | Spieler als Torwart seines Teams setzen |
| `!match switched Name` | Score-Wertung ein-/ausschalten (Toggle) |
| `!match guest "Name" [Score]` | Gastspieler manuell hinzufügen |

### Interaktives Menü

`!cmd` öffnet ein geführtes Poll-Menü mit drei Kategorien: **👤 Spieler**, **⚽ Spieltag**, **📊 Auswertung**.

---

## Wöchentlicher Ablauf

```
Samstag 12:00  →  Bot postet Poll „Kicken Sonntag, DD.MM.YYYY um 10:00"
                   ✅ / ❌ abstimmen
                   🥅-Reaktion = als Torwart melden
                   1️⃣–9️⃣-Reaktion = Gäste hinzufügen

Sonntag 09:00  →  Bot generiert automatisch Vorschlag A
                   → Admin-Team-Poll erscheint in Admin-Gruppe
                   Spieler auswählen + 🔃/🥅/1️⃣–9️⃣/📣 reagieren
                   !team für weitere Vorschläge B, C, …
                   !team vote → Abstimmung unter Vorschlägen

Sonntag 10:00  →  Meistgewählter Vorschlag wird automatisch aktiviert

📣-Reaktion      →  Team wird in Hauptgruppe angekündigt

Nach dem Spiel →  Admin: !result 3:2
                   Bot postet Ergebnis, aktualisiert alle Scores
```

---

## Score-System

Jeder Spieler hat zwei Scores (0–10):

| Score | Beschreibung |
|---|---|
| `field` | Feldspieler-Stärke |
| `gk` | Torwart-Qualität |

**Effektiver Score** für Balancing:
- `can_gk = false` → `field`
- `can_gk = true` → `0,5 × field + 0,5 × gk`

**Neuberechnung nach `!result`:**
```
neuer_score = letzter_score × 0,50
            + Ø_letzte_5_Spiele × 0,30
            + letztes_Spiel × 0,20
```

**Torwart-Zuweisung:**
① 🥅-Reaktion (Freiwillige, nach GK-Score)
② GK-fähige Spieler nach GK-Score
③ Fallback: schwächster Spieler pro Team

---

## Setup

### 1. Repository klonen

```bash
git clone https://github.com/Phat-shot/Teambot.git
cd Teambot
```

### 2. Räume anlegen

Zwei Matrix-Räume – **beide ohne E2E-Verschlüsselung**:
- **Hauptraum** – für alle Spieler
- **Admin-Raum** – nur für Admins

### 3. Konfiguration

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
| `poll_sender_id` | Matrix-ID eines WA-Bridge-Users (optional) |
| `poll_sender_password` | Passwort dieses Users (optional) |

`poll_sender_id/password`: Workaround für mautrix-whatsapp – Polls werden über diesen Account gesendet damit die Bridge sie akzeptiert.

### 4. Starten

```bash
docker compose pull teambot && docker compose up -d teambot
```

### 5. Bot einladen

In beiden Räumen: `/invite @teambot:example.org`

---

## Datenbankstruktur

```
players              – Spieler, field/gk-Score, GK-Fähigkeit
matches              – Matchergebnisse
match_participations – Score-Protokoll pro Spieler/Match
votes                – Vote-Events (Matrix Event-IDs)
vote_responses       – Abstimmungs-Antworten
gk_requests          – 🥅-Meldungen pro Vote
```
