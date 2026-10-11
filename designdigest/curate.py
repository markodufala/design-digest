"""AI curator: Claude looks at contact sheets of today's candidates and scores them.

Runs the Claude Code CLI headless (`claude -p`) so it can use a Claude
subscription token (CLAUDE_CODE_OAUTH_TOKEN) instead of an API key.
"""
import asyncio
import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .models import Item
from .sources import UA
from .taste import Taste

WORK = Path(__file__).resolve().parent.parent / ".curate"
TILE_W, TILE_H, COLS, ROWS = 380, 285, 4, 3
LABEL_H = 34
MODEL = os.environ.get("CURATOR_MODEL", "opus")

PROMPT = """You are a demanding design curator picking a daily inspiration digest for one designer.
They want things that are genuinely different — "wow", surprising, original, crafted —
not generic SaaS landing pages or template-looking work.

Files in the current directory:
- candidates.json — numbered candidates (title, source, section, note, tags)
- sheet_*.jpg — contact sheets; each tile is labelled with its candidate number
{taste_files}
{taste_text}

Read every sheet and candidates.json. For EVERY candidate number, score:
- "wow": 0-10 — originality, visual surprise, craft, memorability (5 = competent but ordinary)
- "fit": 0-10 — how well it matches this person's taste{fit_hint}
- "why": one short, specific line in Slovak (max 90 characters) on what stands out, e.g.
  "Typografia rozbitá do 3D mriežky, reaguje na kurzor" — no generic praise.

Retro items (old websites, web history) should be judged on historical charm and influence.
If a thumbnail failed to load, judge from the title and use wow 4.

Reply with ONLY a JSON object mapping candidate number to scores, no prose, e.g.
{{"1": {{"wow": 8, "fit": 6, "why": "..."}}, "2": {{...}}}}
"""


def available() -> bool:
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")) \
        and shutil.which("claude") is not None


async def _thumbs(items: list[Item]) -> list[Image.Image | None]:
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20, follow_redirects=True) as c:
        async def one(it):
            try:
                r = await c.get(it.image)
                r.raise_for_status()
                im = Image.open(io.BytesIO(r.content)).convert("RGB")
                return ImageOps.fit(im, (TILE_W, TILE_H), centering=(0.5, 0.0))
            except Exception:
                return None
        return await asyncio.gather(*(one(it) for it in items))


def _sheets(items: list[Item], thumbs: list, prefix: str, numbered: bool = True) -> list[str]:
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
    except OSError:
        font = ImageFont.load_default(size=22)
    names, per = [], COLS * ROWS
    for s in range(0, len(items), per):
        chunk = list(zip(items[s:s + per], thumbs[s:s + per]))
        rows = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * TILE_W, rows * (TILE_H + LABEL_H)), "white")
        draw = ImageDraw.Draw(sheet)
        for i, (it, im) in enumerate(chunk):
            x, y = (i % COLS) * TILE_W, (i // COLS) * (TILE_H + LABEL_H)
            if im:
                sheet.paste(im, (x, y + LABEL_H))
            else:
                draw.rectangle([x, y + LABEL_H, x + TILE_W - 1, y + LABEL_H + TILE_H - 1], fill="#ddd")
            label = f"{s + i + 1}  {it.title[:28]}" if numbered else it.title[:32]
            draw.rectangle([x, y, x + TILE_W - 1, y + LABEL_H - 1], fill="black")
            draw.text((x + 8, y + 5), label, fill="white", font=font)
        name = f"{prefix}_{s // per + 1}.jpg"
        sheet.save(WORK / name, quality=82)
        names.append(name)
    return names


def curate(cands: list[Item], taste: Taste | None) -> dict[str, float]:
    """Returns {item_id: 0..1}. Also writes a short Slovak 'why' onto each item."""
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir()

    thumbs = asyncio.run(_thumbs(cands))
    _sheets(cands, thumbs, "sheet")
    (WORK / "candidates.json").write_text(json.dumps({
        str(i + 1): {"title": it.title, "source": it.source, "section": it.section,
                     "note": it.note, "tags": it.tags}
        for i, it in enumerate(cands)
    }, ensure_ascii=False, indent=1))

    taste_files, taste_text, fit_hint = "", "", " (no history yet: use 5 for everyone)"
    if taste:
        for kind, group in (("liked", taste.liked[:12]), ("disliked", taste.disliked[:12])):
            if group:
                _sheets(group, asyncio.run(_thumbs(group)), f"taste_{kind}", numbered=False)
                taste_files += f"- taste_{kind}_*.jpg — things they {kind} recently\n"
        taste_text = "Their recorded taste:\n" + taste.summary()
        fit_hint = " (use the taste sheets and history above)"

    prompt = PROMPT.format(taste_files=taste_files, taste_text=taste_text, fit_hint=fit_hint)
    proc = subprocess.run(
        ["claude", "-p", prompt, "--model", MODEL, "--output-format", "json",
         "--allowedTools", "Read", "--max-turns", "40"],
        cwd=WORK, capture_output=True, text=True, timeout=20 * 60,
    )
    try:
        reply = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError(f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout)[-400:]}")
    result = reply.get("result", "")
    if proc.returncode != 0 or reply.get("is_error"):
        raise RuntimeError(f"claude exited {proc.returncode}: {result[:300]}")
    m = re.search(r"\{.*\}", result, re.S)
    if not m:
        raise RuntimeError(f"no JSON in curator reply: {result[:300]}")
    raw = json.loads(m.group())

    scores = {}
    for i, it in enumerate(cands):
        s = raw.get(str(i + 1))
        if not isinstance(s, dict):
            continue
        wow, fit = float(s.get("wow", 5)), float(s.get("fit", 5))
        scores[it.id] = ((0.6 * wow + 0.4 * fit) if taste else wow) / 10
        it.why = str(s.get("why", ""))[:120]
    return scores
