"""
Matrix Poll Helper – exaktes MSC3381 Format wie von Element Web verwendet.

Quelle: https://github.com/matrix-org/matrix-spec-proposals/blob/main/proposals/3381-polls.md
"""

from typing import List, Tuple


def make_poll(
    question: str,
    answers: List[Tuple[str, str]],
    disclosed: bool = True,
    max_selections: int = 1,
) -> dict:
    kind = (
        "org.matrix.msc3381.poll.disclosed"
        if disclosed
        else "org.matrix.msc3381.poll.undisclosed"
    )

    # Plaintext-Fallback (nummerierte Liste)
    plain = question + "\n" + "\n".join(
        f"{i}. {label}" for i, (_, label) in enumerate(answers, 1)
    )

    return {
        # Pflicht-Textfallback für Clients ohne Poll-Support
        "org.matrix.msc1767.text": plain,
        # Poll-Daten
        "org.matrix.msc3381.poll.start": {
            "kind": kind,
            "max_selections": max_selections,
            "question": {
                "org.matrix.msc1767.text": question,
                "body": question,
                "msgtype": "m.text",
            },
            "answers": [
                {
                    "id": aid,
                    "org.matrix.msc1767.text": label,
                }
                for aid, label in answers
            ],
        },
    }


POLL_EVENT_TYPE = "org.matrix.msc3381.poll.start"
POLL_RESPONSE_TYPES = ("org.matrix.msc3381.poll.response", "m.poll.response")
POLL_RESPONSE_KEYS  = ("org.matrix.msc3381.poll.response", "m.poll.response")
