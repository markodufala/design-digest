"""Personal taste from ♥ / ✕ votes collected by the feedback worker."""
import hashlib
import hmac
import os
from collections import Counter

import httpx

from .models import Item


def sign(item_id: str) -> str:
    """Per-item signature so the worker only accepts votes on items we published."""
    key = os.environ.get("FEEDBACK_KEY", "")
    if not key:
        return ""
    return hmac.new(key.encode(), item_id.encode(), hashlib.sha256).hexdigest()[:16]


def fetch_votes() -> dict[str, dict]:
    """{item_id: {"v": 1 | -1, "at": iso}} — empty if no worker is configured."""
    url = os.environ.get("FEEDBACK_URL", "").rstrip("/")
    if not url:
        return {}
    try:
        r = httpx.get(f"{url}/votes", timeout=20)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        print(f"  ! votes unavailable: {e}")
        return {}


class Taste:
    def __init__(self, votes: dict[str, dict], archive: dict[str, Item]):
        self.votes = votes
        newest = sorted(votes.items(), key=lambda kv: kv[1].get("at", ""), reverse=True)
        self.liked = [archive[i] for i, v in newest if v.get("v") == 1 and i in archive]
        self.disliked = [archive[i] for i, v in newest if v.get("v") == -1 and i in archive]
        self.source_aff = self._affinity(lambda it: [it.source])
        self.tag_aff = self._affinity(lambda it: [t.lower() for t in it.tags])

    def _affinity(self, keys) -> dict[str, float]:
        """(likes - dislikes) / (votes + 3): smoothed, -1..1."""
        plus, minus = Counter(), Counter()
        for it in self.liked:
            plus.update(keys(it))
        for it in self.disliked:
            minus.update(keys(it))
        return {k: (plus[k] - minus[k]) / (plus[k] + minus[k] + 3) for k in plus | minus}

    def __bool__(self) -> bool:
        return bool(self.liked or self.disliked)

    @property
    def hidden(self) -> set[str]:
        return {i for i, v in self.votes.items() if v.get("v") == -1}

    def boost(self, it: Item) -> float:
        tags = [self.tag_aff.get(t.lower(), 0) for t in it.tags]
        b = 1 + 0.5 * self.source_aff.get(it.source, 0) + 0.25 * (sum(tags) / len(tags) if tags else 0)
        return min(max(b, 0.5), 1.6)

    def summary(self, limit: int = 25) -> str:
        """Plain-text taste profile for the AI curator."""
        def line(it):
            extra = ", ".join(x for x in (it.note, ", ".join(it.tags)) if x)
            return f"- {it.title} ({it.source}{'; ' + extra if extra else ''})"
        parts = []
        if self.liked:
            parts += ["LIKED:"] + [line(it) for it in self.liked[:limit]]
        if self.disliked:
            parts += ["DISLIKED:"] + [line(it) for it in self.disliked[:limit]]
        top = sorted(self.source_aff.items(), key=lambda kv: kv[1], reverse=True)
        if top:
            parts.append("Source affinity: " + ", ".join(f"{s} {a:+.2f}" for s, a in top))
        return "\n".join(parts)
