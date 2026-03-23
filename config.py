import yaml
from dataclasses import dataclass


@dataclass
class Config:
    homeserver: str
    user_id: str
    password: str
    room_id: str
    admin_room_id: str = ""

    # Optionaler zweiter Account für Poll-Versand (WA-Bridge-Workaround)
    poll_sender_id: str = ""
    poll_sender_password: str = ""

    db_path: str = "data/teambot.db"

    vote_weekday: int = 5
    vote_hour: int = 12
    vote_minute: int = 0

    team_weekday: int = 6
    team_hour: int = 9
    team_minute: int = 0

    game_hour: int = 10
    game_minute: int = 0

    vote_yes: str = "✅"
    vote_no: str = "❌"


def load_config(path: str = "config.yml") -> Config:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    known = set(Config.__dataclass_fields__)
    data = {k: v for k, v in data.items() if k in known}
    return Config(**data)
