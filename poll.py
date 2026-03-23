"""
Matrix Poll Helper – identisches Format zu Schildi Chat.

Event-Type:  org.matrix.msc3381.poll.start
Content-Key: org.matrix.msc3381.poll.start

Dieses Format wird von:
  ✅ Element Web / Desktop / Mobile
  ✅ Schildi Chat
  ✅ mautrix-whatsapp Bridge
  ✅ FluffyChat, Cinny
korrekt verarbeitet.

Das "stabile" m.poll.start Format ist in der Praxis noch nicht
flächendeckend implementiert – insbesondere Bridges nutzen MSC3381.
"""

from typing import List, Tuple


def make_poll(
    question: str,
    answers: List[Tuple[str, str]],   # [(id, label), ...]
    disclosed: bool = True,
    max_selections: int = 1,
) -> dict:
    """
    Erzeuge poll content im Schildi-kompatiblen MSC3381-Format.

    answers: Liste von (id, anzeigetext)
    """
    kind = (
        "org.matrix.msc3381.poll.disclosed"
        if disclosed
        else "org.matrix.msc3381.poll.undisclosed"
    )

    msc_answers = [
        {
            "id": aid,
            "org.matrix.msc3381.poll.answer.text": label,
            # Auch als body/msgtype für maximale Kompatibilität
            "body": label,
            "msgtype": "m.text",
        }
        for aid, label in answers
    ]

    # Plaintext-Fallback für Clients/Bridges ohne Poll-Support
    plain_lines = [question]
    for i, (_, label) in enumerate(answers, 1):
        plain_lines.append(f"{i}. {label}")
    plain_body = "\n".join(plain_lines)

    return {
        # Poll-Daten im MSC3381-Format
        "org.matrix.msc3381.poll.start": {
            "kind": kind,
            "max_selections": max_selections,
            "question": {
                "body": question,
                "msgtype": "m.text",
            },
            "answers": msc_answers,
        },
        # Pflicht-Felder für m.room.message Fallback
        "msgtype": "m.text",
        "body": plain_body,
    }


# Event-Type für room_send
POLL_EVENT_TYPE = "org.matrix.msc3381.poll.start"

# Response-Event-Types (beide abfangen)
POLL_RESPONSE_TYPES = ("m.poll.response", "org.matrix.msc3381.poll.response")

# Response-Content-Keys (Reihenfolge: unstabil zuerst, da weiter verbreitet)
POLL_RESPONSE_KEYS = ("org.matrix.msc3381.poll.response", "m.poll.response")
