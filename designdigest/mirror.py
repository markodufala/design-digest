"""Copy each pick's image to the feedback worker, cropped to the 4:3 card.

Galleries delete old thumbnails or start blocking hotlinks, and the archive and old
emails would go blank. Pre-cropping also fixes the email, where Gmail ignores
object-fit and short images left an empty band under them.
"""
import asyncio
import hashlib
import io
import os

import httpx
from PIL import Image, ImageOps

from .models import Item
from .sources import UA
from .taste import sign

W, H = 720, 540
CONCURRENCY = 6


def _worker() -> str:
    return os.environ.get("FEEDBACK_URL", "").rstrip("/")


def crop(data: bytes, section: str) -> bytes | None:
    im = Image.open(io.BytesIO(data))
    if getattr(im, "is_animated", False):
        return None  # an animated shot would lose its motion — keep the original
    # old sites put their logo and nav top-left, everything else reads best top-centre
    centering = (0.0, 0.0) if section == "retro" else (0.5, 0.0)
    im = ImageOps.fit(im.convert("RGB"), (W, H), method=Image.Resampling.LANCZOS, centering=centering)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=80, optimize=True, progressive=True)
    return out.getvalue()


async def _one(c: httpx.AsyncClient, it: Item) -> bool | None:
    """True = mirrored, None = deliberately left alone, False = failed."""
    if it.image_orig or not it.image:
        return None  # already mirrored (a re-run of the same day)
    r = await c.get(it.image)
    r.raise_for_status()
    jpeg = crop(r.content, it.section)
    if jpeg is None:
        return None
    key = hashlib.sha1(it.image.encode()).hexdigest()[:20]
    up = await c.post(f"{_worker()}/img", params={"key": key, "s": sign(f"img:{key}")}, content=jpeg,
                      headers={"Content-Type": "image/jpeg"})
    up.raise_for_status()
    if not up.json().get("ok"):
        raise RuntimeError(up.text[:200])
    it.image_orig, it.image = it.image, f"{_worker()}/img/{key}.jpg"
    return True


async def _all(items: list[Item]) -> list:
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as c:
        async def guarded(it):
            async with sem:
                try:
                    return await _one(c, it)
                except Exception as e:  # keep the original URL
                    print(f"  ! mirror {it.id}: {type(e).__name__}: {str(e)[:120]}")
                    return False
        return await asyncio.gather(*(guarded(it) for it in items))


def run(sections: dict[str, list[Item]]) -> dict:
    if not _worker() or not os.environ.get("FEEDBACK_KEY"):
        return {"skipped": True}
    results = asyncio.run(_all([it for group in sections.values() for it in group]))
    return {"mirrored": results.count(True), "failed": results.count(False), "kept": results.count(None)}
