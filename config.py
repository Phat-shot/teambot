import yaml
from dataclasses import dataclass


@dataclass
class Config:
    # Matrix credentials
    homeserver: str
    user_id: str
    password: str
    room_id: str        # Hauptraum (Announcements + öffentliche Befehle)
    admin_room_id: str = ""  # Admin-Raum (leer = kein dedizierter Admin-Raum)
    poll_sender: str = ""   # Matrix-ID über die Polls gesendet werden (für WA-Bridge), leer = Bot selbst

    # Storage
    db_path: str = "data/teambot.db"

    # Vote schedule: Saturday 12:00
    vote_weekday: int = 5   # 0=Mon … 6=Sun
    vote_hour: int = 12
    vote_minute: int = 0

    # Team generation: Sunday 09:00
    team_weekday: int = 6
    team_hour: int = 9
    team_minute: int = 0

    # Game time shown in vote title
    game_hour: int = 10
    game_minute: int = 0

    # Reaction emojis
    vote_yes: str = "✅"
    vote_no: str = "❌"


def load_config(path: str = "config.yml") -> Config:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    # Unbekannte / veraltete Keys entfernen damit Config(**data) nie crasht
    known = {f.name for f in Config.__dataclass_fields__.values()}
    data = {k: v for k, v in data.items() if k in known}
    return Config(**data)
