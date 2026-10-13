"""HTML for the email and the static site (site/)."""
import json
import os
import re
import shutil
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .rank import SECTIONS
from .sources import LABELS
from .taste import sign

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
MONTHS = ["januára", "februára", "marca", "apríla", "mája", "júna", "júla",
          "augusta", "septembra", "októbra", "novembra", "decembra"]
WEEKDAYS = ["pondelok", "utorok", "streda", "štvrtok", "piatok", "sobota", "nedeľa"]

env = Environment(
    loader=FileSystemLoader(ROOT / "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True, lstrip_blocks=True,
)


def nice_date(iso: str, weekday: bool = True) -> str:
    d = date.fromisoformat(iso)
    s = f"{d.day}. {MONTHS[d.month - 1]} {d.year}"
    return f"{WEEKDAYS[d.weekday()].capitalize()}, {s}" if weekday else s


def vote_url(it, v: int) -> str:
    base = os.environ.get("FEEDBACK_URL", "").rstrip("/")
    if not base:
        return ""
    return f"{base}/v?" + urlencode({"id": it.id, "v": v, "s": sign(it.id)})


env.filters["nice_date"] = nice_date
env.filters["sign"] = sign
env.globals.update(SECTIONS=SECTIONS, LABELS=LABELS, vote_url=vote_url)


def build_site(days: list[dict]) -> None:
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "d").mkdir(parents=True)
    tpl = env.get_template("page.html")
    feedback = os.environ.get("FEEDBACK_URL", "").rstrip("/")
    for i, day in enumerate(days):
        html = tpl.render(day=day, days=days, prefix="../", feedback=feedback)
        (SITE / "d" / f"{day['date']}.html").write_text(html)
        if i == 0:
            (SITE / "index.html").write_text(tpl.render(day=day, days=days, prefix="", feedback=feedback))
    # lets the "saved" view render things liked on earlier days or from the email
    archive = {}
    for day in reversed(days):
        for key, items in day["sections"].items():
            for it in items:
                archive[it.id] = {"title": it.title, "url": it.url, "image": it.image,
                                  "source": LABELS.get(it.source, it.source), "section": key}
    (SITE / "items.json").write_text(json.dumps(archive, ensure_ascii=False))
    (SITE / ".nojekyll").touch()


GMAIL_CLIP = 100_000  # Gmail hides everything past ~102 KB behind "[Message clipped]"


def minify(html: str) -> str:
    """Collapse whitespace (HTML ignores it anyway) and tighten inline styles."""
    html = re.sub(r"\s+", " ", html)
    html = re.sub(r">\s+<", "><", html)
    return re.sub(r'style="([^"]*)"', lambda m: 'style="' + re.sub(r"\s*([:;,])\s*", r"\1", m.group(1)).rstrip(";") + '"', html)


UNSUBSCRIBE = "%%UNSUBSCRIBE%%"  # swapped for each subscriber's own link by the mailer


def email(day: dict, site_url: str = "", owner: bool = True) -> tuple[str, str, str]:
    """The owner's edition, or (owner=False) the subscribers' one: same picks, no votes,
    alerts or owner key, and an unsubscribe link (UNSUBSCRIBE placeholder)."""
    total = sum(len(v) for v in day["sections"].values())
    subject = f"Design digest · {nice_date(day['date'], weekday=False)} · {total} vecí"
    broken = [LABELS.get(k, k) for k, h in day["health"].items() if not h["ok"]]
    html = minify(env.get_template("email.html").render(
        day=day, total=total, site_url=site_url.rstrip("/"), broken=broken,
        feedback=bool(os.environ.get("FEEDBACK_URL")), owner=owner, unsubscribe=UNSUBSCRIBE,
    ))
    if len(html.encode()) > GMAIL_CLIP:
        print(f"  ! email is {len(html.encode()) // 1024} KB — Gmail will clip it")
    lines = [subject, ""]
    if owner and day.get("alerts"):
        subject = "⚠ " + subject  # something needs fixing — make it stand out in the inbox
        lines += [f"! {a}" for a in day["alerts"]] + [""]
    for key, items in day["sections"].items():
        if items:
            lines.append(SECTIONS[key][0].upper())
            for it in items:
                lines.append(f"- {it.title} ({LABELS.get(it.source, it.source)}): {it.url}")
                if it.take or it.why:
                    lines.append(f"  {it.take or it.why}")
            lines.append("")
    if not owner:
        lines.append(f"Odhlásiť sa: {UNSUBSCRIBE}")
    return subject, html, "\n".join(lines)
