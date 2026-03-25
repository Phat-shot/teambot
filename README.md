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
| 🔃 **Team-Tausch** | Mit 🔃-Reaktion auf die Team-Ankündigung ins andere Team wechseln |
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

Reaktionen auf den **Vote-Poll** (samstags gepostet):

| Reaktion | Aktion |
|---|---|
| ✅ | Zusage – ich spiele mit |
| ❌ | Absage |
| 🥅 | Als Torwart melden (auch ohne GK-Flag) |
| 1️⃣–9️⃣ | N Gäste hinzufügen (z.B. 2️⃣ → „[User]s Gast 1", „[User]s Gast 2") |

Reaktion auf die **Team-Ankündigung**:

| Reaktion | Aktion |
|---|---|
| 🔃 | Ins andere Team wechseln – automatischer Tausch mit ähnlich bewertetem Gegenspieler |

Wer zum ersten Mal ✅ klickt und noch nicht registriert ist, wird automatisch angelegt (Score 5.0, kein GK) und erhält eine Willkommensnachricht.

---

## Admin-Team-Poll

Nach jedem `!team`-Aufruf postet der Bot automatisch einen Poll in den **Admin-Raum**. Der Poll listet alle Spieler mit Team-Zugehörigkeit (🟡 / 🌈) als Antworten.

**Ablauf:** Spieler im Poll auswählen (Multi-Select), dann mit Emoji reagieren:

| Reaktion | Aktion |
|---|---|
| 🔃 | Selektierte Spieler ins andere Team wechseln (keine Neuberechnung) |
| 🥅 | Selektierte Spieler als Torwart setzen (alter TW rückt ins Feld) |
| 1️⃣–9️⃣ | N Gäste hinzufügen, fair auf beide Teams verteilt |
| 📣 | Aktuelles Team in Hauptgruppe ankündigen |

Nach jeder Aktion: alter Poll gelöscht, neuer Poll gepostet.

---

## Admin-Befehle (nur im Admin-Raum)

| Befehl | Beschreibung |
|---|---|
| `!team` | Neuen Team-Vorschlag generieren (A, B, C, …) |
| `!team A` | Vorschlag A aktivieren |
| `!team vote` | Alle Vorschläge zur Abstimmung stellen |
| `!vote` | Wöchentlichen Vote sofort starten |
| `!result 3:2` | Ergebnis eintragen und Scores neu berechnen |
| `!help` | Alle Befehle anzeigen |
| `!player` | Spielerliste mit Scores |
| `!player add @user:server [Name] [gk]` | Spieler anlegen |
| `!player set Name 7.5` | Basis-Score setzen (manuell, dauerhaft) |
| `!player gk Name` | GK-Bevorzugung ein/aus |
| `!player del Name` | Spieler deaktivieren |
| `!match [N]` | Letzte 5 (oder N) Ergebnisse |
| `!match change Name1 [Name2]` | Spieler tauschen oder verschieben |
| `!match gk Name` | Spieler als Torwart seines Teams setzen |
| `!match switched Name` | Score-Wertung ein-/ausschalten (Toggle) |
| `!match guest "Name" [Score]` | Gastspieler manuell hinzufügen |
| `!cmd` | Geführtes Poll-Menü starten |

---

## Score-System

### Überblick

Jeder Spieler hat zwei Score-Werte:

| Feld | Beschreibung |
|---|---|
| `score_base` | Manuell gesetzter Basis-Score (Admin via `!player set`). Wird **nie** durch Spielergebnisse verändert. |
| `score` | Aktuell berechneter Score. Wird nach jedem Spiel neu berechnet. |

Das `can_gk`-Flag markiert Spieler die bevorzugt ins Tor gehen – sie werden bei der GK-Zuweisung vorrangig berücksichtigt. Ihr Score bleibt einheitlich.

### Match-Score (Elo-Erwartungskorrektur)

Nach jedem Spiel wird für jeden Spieler ein individueller Match-Score berechnet:

```
erwartung = 1 / (1 + 10^((Ø_gegner − Ø_team) / 4))
match_score = clamp(score_base + K × (ergebnis − erwartung) × 10, 0, 10)

ergebnis: Sieg = 1.0  |  Unentschieden = 0.5  |  Niederlage = 0.0
K = 0.3  (Stärke der Anpassung)
```

Ein Spieler der mit dem schwächeren Team gewinnt bekommt mehr Punkte als erwartet. Ein Spieler der mit dem stärkeren Team verliert bekommt weniger Abzug.

### Score-Neuberechnung

Nach jedem Spiel wird `score` aus drei Komponenten zusammengesetzt:

```
score = 50% × score_base
      + 25% × Ø gesamte Match-History (ohne letzte 3 Spiele)
      + 25% × Ø letzte 3 Spiele
```

Die **Langzeit-History** stabilisiert den Score gegen kurzfristige Ausreißer. Die **letzten 3 Spiele** reagieren schnell auf eine gute oder schlechte Phase. Die **Basis** ankert den Score dauerhaft in der manuellen Einschätzung des Admins.

### Beispiel-Rechnung: 10 Spieltage, 12 Spieler

Ausgangslage: 12 Spieler mit Basis-Scores zwischen 3.0 und 8.0. Pro Spieltag kommen 8–10 Spieler.

| Spieler | Basis | Nach 5 Spielen | Nach 10 Spielen | Δ gesamt |
|---|---|---|---|---|
| Alex | 8.0 | 7.8 | 7.7 | −0.3 |
| Ben | 7.5 | 7.6 | 7.7 | +0.2 |
| Chris | 7.0 | 7.1 | 7.0 | ±0.0 |
| Dana | 6.5 | 6.4 | 6.5 | ±0.0 |
| Erik | 6.0 | 6.2 | 6.3 | +0.3 |
| Fabi | 5.5 | 5.4 | 5.5 | ±0.0 |
| Georg | 5.0 | 5.1 | 4.9 | −0.1 |
| Hanna | 4.5 | 4.6 | 4.7 | +0.2 |
| Irina | 4.0 | 4.0 | 4.1 | +0.1 |
| Jonas | 3.5 | 3.7 | 3.8 | +0.3 |
| Kai | 3.0 | 3.1 | 3.0 | ±0.0 |
| Lena | 2.5 | 2.6 | 2.7 | +0.2 |

Werte sind typische Simulationsergebnisse – variieren je nach Spielverlauf.

**Detailbeispiel: Jonas (Basis 3.5) gewinnt mit dem schwächeren Team**

```
Teamstärke:  🟡 Ø 4.2  vs  🌈 Ø 6.1  → Jonas spielt in Team 🟡
Erwartung:   1 / (1 + 10^((6.1 − 4.2) / 4)) = 0.26  (26% Gewinnchance)
Ergebnis:    Sieg → 1.0
Match-Score: 3.5 + 0.3 × (1.0 − 0.26) × 10 = 3.5 + 2.22 = 5.72

History vor diesem Spiel (letzte 3): [3.4, 3.6, 3.5]  Ø = 3.5
History gesamt (davor):              [3.5, 3.4]        Ø = 3.45
Neue History letzte 3:               [3.6, 3.5, 5.72]  Ø = 4.27

Neuer Score: 50% × 3.5 + 25% × 3.45 + 25% × 4.27
           = 1.75 + 0.86 + 1.07 = 3.68
```

Der Score steigt von 3.5 auf 3.68 – ein deutlicher Boost für den Überraschungssieg, aber die Basis hält den Score geerdet.

**Detailbeispiel: Alex (Basis 8.0) verliert mit dem stärkeren Team**

```
Teamstärke:  🟡 Ø 7.1  vs  🌈 Ø 4.8  → Alex spielt in Team 🟡
Erwartung:   1 / (1 + 10^((4.8 − 7.1) / 4)) = 0.82  (82% Gewinnchance)
Ergebnis:    Niederlage → 0.0
Match-Score: 8.0 + 0.3 × (0.0 − 0.82) × 10 = 8.0 − 2.46 = 5.54

History letzte 3 (neu): [7.9, 8.1, 5.54]  Ø = 7.18
History gesamt (davor): [8.0, 7.8, 8.2]   Ø = 8.0

Neuer Score: 50% × 8.0 + 25% × 8.0 + 25% × 7.18
           = 4.0 + 2.0 + 1.795 = 7.80
```

Alex fällt von 8.0 auf 7.80 – spürbare Strafe für die unerwartete Niederlage.

### Besonderheiten

Wenn ein Spieler weniger als 3 Spiele in der History hat, wird für den fehlenden Anteil die Basis verwendet. Das verhindert extreme Ausschläge bei neuen Spielern.

Gäste erhalten keinen Score-Update – ihr temporärer Score (5.0) fließt nur in die Team-Balance ein.

---

## Wöchentlicher Ablauf

```
Samstag 12:00  →  Bot postet Poll „Kicken Sonntag, DD.MM.YYYY um 10:00"
                   ✅ / ❌ abstimmen
                   🥅 = als Torwart melden
                   1️⃣–9️⃣ = Gäste hinzufügen

Sonntag 09:00  →  Bot generiert automatisch Vorschlag A
                   → Admin-Team-Poll erscheint in Admin-Gruppe
                   Spieler auswählen + 🔃/🥅/1️⃣–9️⃣ reagieren
                   !team für weitere Vorschläge B, C, …

Sonntag 10:00  →  Meistgewählter Vorschlag wird automatisch aktiviert

📣-Reaktion      →  Team wird in Hauptgruppe angekündigt
🔃-Reaktion      →  Spieler wechselt Team (in Hauptgruppe auf Team-Nachricht)

Nach dem Spiel →  Admin: !result 3:2
                   Bot postet Ergebnis in Haupt- und Admin-Raum
                   Scores werden automatisch neu berechnet
```

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
players              – Spieler, score (aktuell), score_base (manuell), can_gk
matches              – Matchergebnisse
match_participations – Match-Score pro Spieler (Elo-berechnet)
votes                – Vote-Events (Matrix Event-IDs)
vote_responses       – Abstimmungs-Antworten
gk_requests          – 🥅-Meldungen pro Vote
```
