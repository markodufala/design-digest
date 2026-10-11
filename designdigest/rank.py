"""Turn raw per-source lists into one balanced daily digest."""
import random
from collections import defaultdict

from .models import Item

# How many picks per section, in display order.
SECTIONS = {
    "web": ("Web — top weby dňa", 8),
    "visual": ("Vizuál & shots", 6),
    "ui": ("UI komponenty", 4),
    "retro": ("Retro — z archívu", 3),
}

# Nudges trusted / curated sources up a bit.
SOURCE_WEIGHT = {
    "awwwards": 1.15,
    "dribbble": 1.1,
    "godly": 1.05,
    "21st": 1.0,
    "landbook": 1.0,
    "minimalgallery": 0.95,
    "onepagelove": 0.95,
    "webdesignmuseum": 1.0,
}

# Sections allowed to repeat already-seen items when nothing new is left.
RECYCLE = {"retro"}


def score(items: list[Item]) -> None:
    """Normalise inside each source to 0..1 so big sources don't drown small ones."""
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
            it.score = round(pct * SOURCE_WEIGHT.get(source, 1.0), 4)


def pick(items: list[Item], seen: set[str]) -> dict[str, list[Item]]:
    """Best unseen items per section, round-robin across sources for variety."""
    score(items)
    out = {}
    for section, (_, quota) in SECTIONS.items():
        queues = defaultdict(list)
        for it in sorted(items, key=lambda it: it.score, reverse=True):
            if it.section == section and it.id not in seen:
                queues[it.source].append(it)
        chosen = []
        # sources with the strongest top item go first in each round
        order = sorted(queues, key=lambda s: queues[s][0].score, reverse=True)
        while len(chosen) < quota and any(queues.values()):
            for s in order:
                if queues[s] and len(chosen) < quota:
                    chosen.append(queues[s].pop(0))
        if section in RECYCLE and len(chosen) < quota:
            # the archive pool is finite — once exhausted, show old picks again
            rest = [it for it in items if it.section == section and it not in chosen]
            random.shuffle(rest)
            chosen += rest[: quota - len(chosen)]
        out[section] = chosen
    return out
