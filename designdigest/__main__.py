"""Daily run:  python -m designdigest [--no-email] [--only dribbble,godly]"""
import argparse
import asyncio
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import curate, mailer, rank, render, sources, store
from .taste import Taste, fetch_votes

TZ = ZoneInfo("Europe/Bratislava")


async def fetch_all(names: list[str]):
    items, health = [], {}
    async with sources.client() as c:
        async def run(name):
            t = time.monotonic()
            try:
                got = await sources.SOURCES[name](c)
                health[name] = {"ok": True, "count": len(got), "secs": round(time.monotonic() - t, 1)}
                return got
            except Exception as e:  # one broken source must not kill the digest
                health[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
                return []
        for got in await asyncio.gather(*(run(n) for n in names)):
            items.extend(got)
    return items, health


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-email", action="store_true", help="build the site only")
    ap.add_argument("--email-only", action="store_true", help="send the latest archived day, don't scrape")
    ap.add_argument("--only", help="comma-separated sources (for testing)")
    ap.add_argument("--no-ai", action="store_true", help="skip the Claude curator")
    args = ap.parse_args()

    if args.email_only:
        return send_latest()

    today = datetime.now(TZ).date()
    names = args.only.split(",") if args.only else list(sources.SOURCES)
    items, health = asyncio.run(fetch_all(names))
    for name, h in health.items():
        print(f"  {'✓' if h['ok'] else '✗'} {name:16} {h.get('count', h.get('error'))}")

    profile = Taste(fetch_votes(), store.load_archive())
    print(f"  taste: {len(profile.liked)} ♥ / {len(profile.disliked)} ✕")

    # re-running the same day may reuse today's picks; ✕-ed things never come back
    seen = {k for k, d in store.load_seen().items() if d != today.isoformat()} | profile.hidden
    rank.score(items, profile)

    curator = {"ok": False}
    if args.no_ai or not curate.available():
        curator["error"] = "disabled" if args.no_ai else "no Claude token / CLI"
    else:
        shortlist = rank.candidates(items, seen)
        t = time.monotonic()
        try:
            ai = curate.curate(shortlist, profile or None)
            rank.score(items, profile, ai)
            curator = {"ok": True, "model": curate.MODEL, "rated": len(ai), "secs": round(time.monotonic() - t)}
        except Exception as e:  # fall back to the heuristic ranking
            curator["error"] = f"{type(e).__name__}: {e}"[:300]
    print(f"  curator: {curator}")

    picked = rank.pick(items, seen)
    total = sum(len(v) for v in picked.values())
    if total == 0:
        print("Nothing to send — all sources failed?", file=sys.stderr)
        return 1

    store.save_day(today, picked, health, curator)
    days = store.load_days()
    render.build_site(days)
    print(f"Saved {today}: {total} items → site/")

    if not args.no_email:
        return send_latest()
    return 0


def send_latest() -> int:
    days = store.load_days()
    if not days:
        print("No archived day to send.", file=sys.stderr)
        return 1
    subject, html, plain = render.email(days[0], site_url=os.environ.get("SITE_URL", ""))
    print("Mail:", mailer.send(subject, html, plain))
    return 0


if __name__ == "__main__":
    sys.exit(main())
