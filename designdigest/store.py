"""Archive lives as plain JSON in data/ — committed back to the repo daily."""
import json
from datetime import date, timedelta
from pathlib import Path

from .models import Item

DATA = Path(__file__).resolve().parent.parent / "data"
DAYS = DATA / "days"
SEEN = DATA / "seen.json"
SEEN_TTL_DAYS = 365


def load_seen() -> dict[str, str]:
    return json.loads(SEEN.read_text()) if SEEN.exists() else {}


def save_day(day: date, sections: dict[str, list[Item]], health: dict) -> None:
    DAYS.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": day.isoformat(),
        "sections": {s: [it.to_dict() for it in items] for s, items in sections.items()},
        "health": health,
    }
    (DAYS / f"{day.isoformat()}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))

    seen = load_seen()
    for items in sections.values():
        for it in items:
            seen.setdefault(it.id, day.isoformat())
    cutoff = (day - timedelta(days=SEEN_TTL_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    SEEN.write_text(json.dumps(seen, indent=0, sort_keys=True))


def load_days() -> list[dict]:
    """All archived days, newest first."""
    days = [json.loads(p.read_text()) for p in sorted(DAYS.glob("*.json"), reverse=True)]
    for d in days:
        d["sections"] = {s: [Item.from_dict(x) for x in items] for s, items in d["sections"].items()}
    return days
