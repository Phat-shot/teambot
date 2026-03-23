"""
Matrix Poll Helper – erzeugt konformes m.poll.start Event.

Verwendet den stabilen Event-Type `m.poll.start` (Matrix 1.7+) mit
zusätzlichem unstabilem `org.matrix.msc3381.poll.start` Fallback.
Dieses Format wird von Element, Schildi und WA-Bridge korrekt verarbeitet.
"""

from typing import List, Tuple


def make_poll(
    question: str,
    answers: List[Tuple[str, str]],   # [(id, label), ...]
    disclosed: bool = True,
    max_selections: int = 1,
) -> dict:
    """
    Erzeuge korrekten m.poll.start Event-Content.

    answers: Liste von (id, anzeigetext) – IDs werden in Antworten zurückgegeben.
    """
    kind_stable   = "m.poll.disclosed"   if disclosed else "m.poll.undisclosed"
    kind_unstable = "org.matrix.msc3381.poll.disclosed" if disclosed else "org.matrix.msc3381.poll.undisclosed"

    # Stabiles Format (Matrix 1.7 / Element 1.11+)
    stable_answers = [
        {
            "m.id": aid,
            "m.text": [{"body": label, "mimetype": "text/plain"}],
        }
        for aid, label in answers
    ]

    # Unstabiles Fallback-Format (ältere Clients)
    unstable_answers = [
        {
            "id": aid,
            "org.matrix.msc3381.poll.answer.text": label,
        }
        for aid, label in answers
    ]

    # Plaintext-Fallback für Bridges die keine Polls kennen
    plain_lines = [question]
    for i, (_, label) in enumerate(answers, 1):
        plain_lines.append(f"{i}. {label}")
    plain_body = "\n".join(plain_lines)

    return {
        # Stabiler Content-Key
        "m.poll": {
            "kind": kind_stable,
            "max_selections": max_selections,
            "question": {
                "m.text": [{"body": question, "mimetype": "text/plain"}],
                "body": question,
            },
            "answers": stable_answers,
        },
        # Unstabiler Fallback
        "org.matrix.msc3381.poll.start": {
            "kind": kind_unstable,
            "max_selections": max_selections,
            "question": {"body": question},
            "answers": unstable_answers,
        },
        # Text-Fallback für Clients ohne Poll-Support
        "msgtype": "m.text",
        "body": plain_body,
        "format": "org.matrix.custom.html",
        "formatted_body": "<b>" + question + "</b><br>" + "<br>".join(
            f"{i}. {label}" for i, (_, label) in enumerate(answers, 1)
        ),
    }


# Event-Type für room_send (stabil)
POLL_EVENT_TYPE = "m.poll.start"

# Response-Event-Types (beide abfangen)
POLL_RESPONSE_TYPES = ("m.poll.response", "org.matrix.msc3381.poll.response")

# Response-Content-Keys (beide auslesen)
POLL_RESPONSE_KEYS = ("m.poll.response", "org.matrix.msc3381.poll.response")
