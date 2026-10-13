"""Daily run:  python -m designdigest [--no-email] [--only dribbble,godly]"""
import argparse
import asyncio
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import alerts, analyze, curate, mailer, mirror, rank, render, sources, store
from .taste import Taste, fetch_profile, fetch_votes, profile_due, save_profile, worker_status

TZ = ZoneInfo("Europe/Bratislava")
RETRY_AFTER = 15  # seconds before failed sources get a second try


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
        # in CI a different source times out most days — usually while a dozen headless
        # browsers start at once; a second, quieter try gets most of them back
        failed = [n for n in names if not health[n]["ok"]]
        if failed:
            await asyncio.sleep(RETRY_AFTER)
            for got in await asyncio.gather(*(run(n) for n in failed)):
                items.extend(got)
            for n in failed:
                health[n]["retried"] = True
    return items, health


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-email", action="store_true", help="build the site only")
    ap.add_argument("--email-only", action="store_true", help="send the latest archived day, don't scrape")
    ap.add_argument("--only", help="comma-separated sources (for testing)")
    ap.add_argument("--no-ai", action="store_true", help="skip the Claude curator")
    ap.add_argument("--no-rozbor", action="store_true", help="skip visiting the live sites")
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
    ai = not args.no_ai and curate.available()

    taste_profile = fetch_profile()
    taste_info = {"text": taste_profile.get("text", ""), "updated": False}
    if ai and profile_due(taste_profile, profile, today.isoformat()):
        try:
            taste_info = {"text": save_profile(curate.write_profile(profile, taste_info["text"]))["text"],
                          "updated": True}
            print("  taste profile rewritten")
        except Exception as e:  # keep the old one
            print(f"  ! taste profile: {type(e).__name__}: {e}"[:300])

    # re-running the same day may reuse today's picks; ✕-ed things never come back
    seen = {k for k, d in store.load_seen().items() if d != today.isoformat()} | profile.hidden
    rank.score(items, profile)

    curator = {"ok": False}
    if not ai:
        curator["error"] = "disabled" if args.no_ai else "no Claude token / CLI"
    else:
        shortlist = rank.candidates(items, seen)
        t = time.monotonic()
        try:
            verdict = curate.curate(shortlist, profile or None, taste_info["text"])
            rank.score(items, profile, verdict)
            curator = {"ok": True, "model": curate.MODEL, "rated": len(verdict), "secs": round(time.monotonic() - t)}
        except Exception as e:  # fall back to the heuristic ranking
            curator["error"] = f"{type(e).__name__}: {e}"[:300]
    print(f"  curator: {curator}")

    picked = rank.pick(items, seen)
    total = sum(len(v) for v in picked.values())
    if total == 0:
        print("Nothing to send — all sources failed?", file=sys.stderr)
        return 1

    analysis = {"skipped": True}
    if not args.no_rozbor:
        try:
            analysis = analyze.run(picked, use_ai=ai)
        except Exception as e:  # never lose the digest over the extras
            analysis = {"error": f"{type(e).__name__}: {e}"[:300]}
        print(f"  rozbor: {analysis}")
        if dropped := rank.dedupe(picked):  # the rozbor found that two galleries showed one site
            analysis["duplicates_dropped"] = dropped
            print(f"  dropped {dropped} duplicate(s)")

    mirrored = mirror.run(picked)
    print(f"  images: {mirrored}")

    warnings = alerts.collect(today, worker_status(), curator, analysis, mirrored)
    for w in warnings:
        print(f"  ! {w}")
    store.save_day(today, picked, health, curator, analysis=analysis, taste=taste_info,
                    images=mirrored, alerts=warnings)
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
