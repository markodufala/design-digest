"""Turn raw per-source lists into one balanced daily digest."""
import random
from collections import defaultdict

from .models import Item

# How many picks per section, in display order.
SECTIONS = {
    "wow": ("WOW — experimenty & úlety", 6),
    "web": ("Web — top weby dňa", 8),
    "visual": ("Vizuál & shots", 6),
    "ui": ("UI komponenty", 3),
    "type": ("Typo — písmo v praxi", 3),
    "retro": ("Retro — z archívu", 3),
}

# Nudges trusted / curated sources up a bit.
SOURCE_WEIGHT = {
    "awwwards": 1.15,
    "codrops": 1.15,
    "gsap": 1.1,
    "dribbble": 1.1,
    "godly": 1.05,
    "cssda": 1.05,
    "bestwebsite": 1.05,
    "fontsinuse": 1.05,
    "21st": 1.0,
    "landbook": 1.0,
    "webflow": 1.0,
    "cosmos": 1.0,
    "brutalist": 1.0,
    "webdesignmuseum": 1.0,
    "minimalgallery": 0.95,
    "onepagelove": 0.95,
    "darkdesign": 0.9,
}

# Sections allowed to repeat already-seen items when nothing new is left.
RECYCLE = {"retro"}

# How much an AI score (0..1) weighs against the source-popularity score.
AI_WEIGHT = 0.7
UNRATED = 0.3


def score(items: list[Item], taste=None, ai: dict | None = None) -> None:
    """Normalise inside each source to 0..1 so big sources don't drown small ones,
    then blend in personal taste and (optionally) the AI curator's verdict."""
    by_source = defaultdict(list)
    for it in items:
        by_source[it.source].append(it)
    for source, group in by_source.items():
        n = len(group)
        if all(it.popularity is not None for it in group):
            ordered = sorted(group, key=lambda it: it.popularity, reverse=True)
        else:
            ordered = sorted(group, key=lambda it: it.position)
        for rank, it in enumerate(ordered):
            pct = 1 - rank / max(n - 1, 1)
            it.score = pct * SOURCE_WEIGHT.get(source, 1.0)
            if taste:
                it.score *= taste.boost(it)
            if ai:
                # items the curator never saw count as below average, so rated ones win
                it.score = (1 - AI_WEIGHT) * min(it.score, 1.0) + AI_WEIGHT * ai.get(it.id, UNRATED)
            it.score = round(it.score, 4)


def candidates(items: list[Item], seen: set[str], per_section: int = 3) -> list[Item]:
    """Shortlist for the AI curator: a few times each section's quota, diverse by source."""
    out = []
    for section, (_, quota) in SECTIONS.items():
        out += _round_robin([it for it in items if it.section == section and it.id not in seen],
                            quota * per_section)
    return out


def pick(items: list[Item], seen: set[str]) -> dict[str, list[Item]]:
    """Best unseen items per section, round-robin across sources for variety."""
    out = {}
    for section, (_, quota) in SECTIONS.items():
        pool = [it for it in items if it.section == section]
        chosen = _round_robin([it for it in pool if it.id not in seen], quota)
        if section in RECYCLE and len(chosen) < quota:
            # the archive pool is finite — once exhausted, show old picks again
            rest = [it for it in pool if it not in chosen]
            random.shuffle(rest)
            chosen += rest[: quota - len(chosen)]
        out[section] = sorted(chosen, key=lambda it: it.score, reverse=True)
    return out


def _round_robin(pool: list[Item], quota: int) -> list[Item]:
    queues = defaultdict(list)
    for it in sorted(pool, key=lambda it: it.score, reverse=True):
        queues[it.source].append(it)
    chosen = []
    # sources with the strongest top item go first in each round
    order = sorted(queues, key=lambda s: queues[s][0].score, reverse=True)
    while len(chosen) < quota and any(queues.values()):
        for s in order:
            if queues[s] and len(chosen) < quota:
                chosen.append(queues[s].pop(0))
    return chosen
