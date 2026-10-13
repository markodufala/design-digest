"""Rozbor: open each pick's live site, read its fonts, palette and stack,
then let Claude say what's worth stealing from it.

Runs after picking, so it only touches the ~25 things that actually get published.
"""
import asyncio
import io
import json
import re
import time
from pathlib import Path

from PIL import Image, ImageOps

from . import curate
from .models import Item
from .sources import UA

WORK = Path(__file__).resolve().parent.parent / ".analyze"
LIVE_SECTIONS = {"web", "wow"}   # the rest are shots, components or long-dead sites
CONCURRENCY = 4
SOCIAL = re.compile(r"(twitter|x|instagram|facebook|linkedin|dribbble|behance|youtube|vimeo|github|tiktok|pinterest)\.com")

# Records which canvas contexts the page asks for — the most reliable sign of WebGL.
INIT_JS = """(() => {
  const get = HTMLCanvasElement.prototype.getContext;
  window.__dd_ctx = [];
  HTMLCanvasElement.prototype.getContext = function (type, ...rest) {
    const ctx = get.call(this, type, ...rest);
    if (ctx && !window.__dd_ctx.includes(type)) window.__dd_ctx.push(type);
    return ctx;
  };
})()"""

# Gallery detail pages → the "visit site" link.
VISIT_JS = """() => {
  const host = location.hostname.replace(/^www\\./, '');
  const re = /visit|launch|live site|view (the )?(site|website)|open (site|website)|go to (site|website)/i;
  const a = [...document.querySelectorAll('a[href^="http"]')].find(a =>
    !a.hostname.endsWith(host) &&
    re.test([a.textContent, a.getAttribute('aria-label'), a.title].join(' ')));
  return a ? a.href : null;
}"""

# Is a preloader / intro screen still covering the page?
BUSY_JS = """() => {
  const re = /preload|loader|loading|splash|intro-screen/i;
  return [...document.querySelectorAll('body *')].some(el => {
    if (!re.test((el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : ''))) return false;
    const cs = getComputedStyle(el), r = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' && parseFloat(cs.opacity) > 0.1
      && r.width * r.height > innerWidth * innerHeight * 0.6;
  });
}"""

# Hide cookie / consent banners for the screenshot — hidden, never accepted.
HIDE_CONSENT_JS = """() => {
  const known = '#onetrust-consent-sdk, #CybotCookiebotDialog, #usercentrics-root, #didomi-host, .cc-window, '
    + '.osano-cm-window, .fc-consent-root, .qc-cmp2-container, #cookiescript_injected, .cky-consent-container, '
    + '#hs-eu-cookie-confirmation, .termly-styles-root, iframe[src*="consent"]';
  const hide = el => el.style.setProperty('display', 'none', 'important');
  document.querySelectorAll(known).forEach(hide);
  const re = /cookie|consent|gdpr|privacy preferences|we use cookies/i;
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed' && cs.position !== 'sticky') continue;
    const r = el.getBoundingClientRect();
    if (r.width * r.height < innerWidth * innerHeight * 0.7 && re.test(el.textContent.slice(0, 600))) hide(el);
  }
}"""

PROBE_JS = """() => {
  // fonts, weighted by how much text they set and how big
  const weight = {};
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n, count = 0;
  while ((n = walker.nextNode()) && count++ < 4000) {
    const t = n.textContent.trim();
    if (t.length < 2 || !n.parentElement) continue;
    const el = n.parentElement, r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.opacity === '0') continue;
    const fam = cs.fontFamily.split(',')[0].trim().replace(/^["']|["']$/g, '');
    const size = parseFloat(cs.fontSize) || 16;
    weight[fam] = (weight[fam] || 0) + Math.min(t.length, 400) * size;
  }

  const src = [...document.scripts].map(s => s.src).join(' ').toLowerCase()
    + ' ' + [...document.querySelectorAll('link[href]')].map(l => l.href).join(' ').toLowerCase();
  const html = document.documentElement, gen = (document.querySelector('meta[name=generator]')?.content || '').toLowerCase();
  const W = window, tech = [];
  const has = (name, cond) => { try { if (cond()) tech.push(name); } catch {} };
  has('Webflow', () => html.hasAttribute('data-wf-site') || gen.includes('webflow'));
  has('Framer', () => gen.includes('framer') || src.includes('framerusercontent'));
  has('Next.js', () => W.__NEXT_DATA__ || W.next || src.includes('/_next/'));
  has('Nuxt', () => W.__NUXT__ || W.$nuxt || src.includes('/_nuxt/'));
  has('Astro', () => document.querySelector('astro-island') || gen.includes('astro'));
  has('SvelteKit', () => Object.keys(W).some(k => k.startsWith('__sveltekit')));
  has('WordPress', () => src.includes('wp-content') || gen.includes('wordpress'));
  has('Shopify', () => W.Shopify);
  has('Squarespace', () => W.Static?.SQUARESPACE_CONTEXT || gen.includes('squarespace'));
  has('Wix', () => gen.includes('wix'));
  has('GSAP', () => W.gsap || W.TweenMax || /gsap|greensock/.test(src));
  has('ScrollTrigger', () => W.ScrollTrigger || src.includes('scrolltrigger'));
  has('Three.js', () => W.__THREE__ || W.THREE || /three(\\.module)?(\\.min)?\\.js|\\/three@/.test(src));
  has('OGL', () => /\\bogl\\b/.test(src));
  has('PixiJS', () => W.PIXI || /pixi(\\.min)?\\.js/.test(src));
  has('Babylon.js', () => W.BABYLON);
  has('Spline', () => document.querySelector('spline-viewer') || src.includes('splinetool') || src.includes('spline.design'));
  has('Unicorn Studio', () => W.UnicornStudio || src.includes('unicornstudio'));
  has('Rive', () => W.rive || src.includes('rive.app') || src.includes('@rive-app'));
  has('Lottie', () => W.lottie || W.bodymovin || document.querySelector('lottie-player, dotlottie-player'));
  has('Lenis', () => W.lenis || W.Lenis || html.classList.contains('lenis'));
  has('Locomotive Scroll', () => W.LocomotiveScroll || html.classList.contains('has-scroll-smooth'));
  has('Barba.js', () => W.barba || document.querySelector('[data-barba]'));
  has('Swup', () => W.swup || document.querySelector('[id^=swup]'));
  has('Video', () => [...document.querySelectorAll('video')].some(v => v.autoplay && v.getBoundingClientRect().width > 300));
  return { fonts: weight, tech, ctx: W.__dd_ctx || [] };
}"""

GENERIC_FONTS = {
    "serif", "sans-serif", "monospace", "system-ui", "-apple-system", "blinkmacsystemfont", "cursive",
    "fantasy", "emoji", "math", "inherit", "initial", "ui-sans-serif", "ui-serif", "ui-monospace",
    "segoe ui", "apple color emoji",
}
ICON_FONT = re.compile(r"icon|awesome|material symbols|glyph|swiper|slick", re.I)
STYLE_SUFFIX = re.compile(
    r"[\s_-]*(regular|medium|bold|light|book|black|thin|hairline|semi\s?bold|extra\s?bold|ultra\s?bold"
    r"|extra\s?light|ultra\s?light|heavy|italic|roman|variable|vf|web|webfont|\d{3})$", re.I)
WEBGL_LIBS = {"Three.js", "OGL", "PixiJS", "Babylon.js", "Spline", "Unicorn Studio"}


def clean_font(name: str) -> str:
    n = name.strip().strip("\"'")
    if not n or "fallback" in n.lower() or n.lower() in GENERIC_FONTS:
        return ""
    n = re.sub(r"^_+", "", n)                     # next/font: "__Inter_d65c78"
    n = re.sub(r"_[0-9a-f]{5,8}$", "", n)
    for _ in range(3):                            # "NeueMontreal-MediumItalic"
        n = STYLE_SUFFIX.sub("", n) or n
    if " " not in n:                              # "GT-America" / "NeueMontreal"
        n = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", n.replace("-", " "))
    n = n.replace("_", " ").strip(" -")
    if n.lower() in GENERIC_FONTS or ICON_FONT.search(n):
        return ""
    return n


def top_fonts(weights: dict[str, float], limit: int = 3) -> list[str]:
    merged: dict[str, float] = {}
    for raw, w in weights.items():
        if name := clean_font(raw):
            name = name.title() if name == name.lower() else name   # "neulis sans"
            merged[name] = merged.get(name, 0) + w
    total = sum(merged.values()) or 1
    ranked = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)
    return [n for n, w in ranked if w / total >= 0.06][:limit]


def palette(im: Image.Image, n: int = 5) -> list[str]:
    """Most used colours, near-duplicates merged."""
    im = im.convert("RGB")
    im.thumbnail((200, 200))
    q = im.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
    pal, counts = q.getpalette(), sorted(q.getcolors(), reverse=True)
    total = sum(c for c, _ in counts)
    out: list[tuple] = []
    for c, idx in counts:
        rgb = tuple(pal[idx * 3: idx * 3 + 3])
        if c / total < 0.015 or any(sum((a - b) ** 2 for a, b in zip(rgb, o)) < 40 ** 2 for o in out):
            continue
        out.append(rgb)
        if len(out) == n:
            break
    return ["#%02x%02x%02x" % c for c in out]


async def _visit(browser, it: Item) -> Image.Image | None:
    """Fills it.link / fonts / tech from the live site; returns an above-the-fold screenshot."""
    ctx = await browser.new_context(user_agent=UA, viewport={"width": 1440, "height": 900}, locale="en-US")
    await ctx.add_init_script(INIT_JS)
    page = await ctx.new_page()
    try:
        if not it.link:
            await page.goto(it.url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1500)
            link = await page.evaluate(VISIT_JS)
            if not link or SOCIAL.search(link):
                return None
            it.link = link
        try:
            await page.goto(it.link, wait_until="load", timeout=30000)
        except Exception:
            pass  # slow assets — whatever rendered by now is enough
        await page.wait_for_timeout(3500)  # intro animations, font swap
        for _ in range(12):                # long preloaders get up to 6 s more
            if not await page.evaluate(BUSY_JS):
                break
            await page.wait_for_timeout(500)
        await page.mouse.wheel(0, 1200)    # wakes lazy WebGL / scroll libs
        await page.wait_for_timeout(1200)
        probe = await page.evaluate(PROBE_JS)
        await page.mouse.wheel(0, -1200)
        await page.evaluate("window.scrollTo(0, 0)")  # smooth-scroll libs eat part of the wheel
        await page.wait_for_timeout(1000)
        await page.evaluate(HIDE_CONSENT_JS)
        shot = await page.screenshot(type="jpeg", quality=80)
        if not it.fonts:
            it.fonts = top_fonts(probe["fonts"])
        tech = probe["tech"]
        if any(c.startswith("webgpu") for c in probe["ctx"]):
            tech.append("WebGPU")
        elif any("webgl" in c for c in probe["ctx"]) and not WEBGL_LIBS & set(tech):
            tech.append("WebGL")
        it.tech = tech[:6]
        return Image.open(io.BytesIO(shot))
    finally:
        await ctx.close()


async def _visit_all(items: list[Item]) -> list[Image.Image | None]:
    from playwright.async_api import async_playwright

    sem = asyncio.Semaphore(CONCURRENCY)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        async def one(it):
            if it.section not in LIVE_SECTIONS:
                return None
            async with sem:
                try:
                    return await asyncio.wait_for(_visit(browser, it), timeout=60)
                except Exception as e:  # dead site, bot wall, timeout — just skip the rozbor
                    print(f"  ! rozbor {it.id}: {type(e).__name__}: {str(e)[:120]}")
                    return None
        shots = await asyncio.gather(*(one(it) for it in items))
        await browser.close()
    return shots


TAKE_PROMPT = """You are a sharp design mentor. For each numbered item below, a designer gets a daily
inspiration digest and wants to know what is worth stealing from it.

Files in the current directory:
- items.json — numbered items: title, source, section, fonts and tech detected on the live site, earlier note
- sheet_*.jpg — the live site above the fold where we have it, otherwise the gallery thumbnail
- thumb_*.jpg — the gallery thumbnails (useful when the live shot caught a preloader or cookie wall)
Tiles are labelled with the item number.

For EVERY number write "take": one specific, practical line in Slovak (max 100 characters) naming the
design decision or technique to borrow — layout, type pairing, colour move, motion idea, interaction.
Use the detected fonts/tech when they explain the effect, but don't just list them. No generic praise.
Good: "Obrovský serif cez celú šírku, všetko ostatné drobný mono — kontrast robí celú stránku"

Reply with ONLY a JSON object, e.g. {{"1": {{"take": "..."}}, "2": {{"take": "..."}}}}
"""


def run(sections: dict[str, list[Item]], use_ai: bool) -> dict:
    """Mutates the picked items in place; returns a summary for the day's record."""
    t = time.monotonic()
    items = [it for group in sections.values() for it in group]
    shots = asyncio.run(_visit_all(items))
    thumbs = asyncio.run(curate._thumbs(items))
    tiles = []
    for it, shot, thumb in zip(items, shots, thumbs):
        it.palette = palette(shot) if shot else []
        if len(it.palette) < 2 and thumb is not None:  # blank page or a preloader caught mid-way
            it.palette = palette(thumb)
        # whole width of the live page, top-aligned — cropping the sides hides the layout
        tiles.append(ImageOps.pad(shot.convert("RGB"), (curate.TILE_W, curate.TILE_H), color="white",
                                  centering=(0.5, 0.0)) if shot else thumb)
    out = {"visited": sum(s is not None for s in shots), "items": len(items)}

    if use_ai:
        try:
            curate._fresh(WORK)
            curate._sheets(items, tiles, "sheet", out=WORK)
            curate._sheets(items, thumbs, "thumb", out=WORK)
            (WORK / "items.json").write_text(json.dumps({
                str(i + 1): {"title": it.title, "source": it.source, "section": it.section,
                             "fonts": it.fonts, "tech": it.tech, "note": it.note, "earlier": it.why}
                for i, it in enumerate(items)
            }, ensure_ascii=False, indent=1))
            raw = curate.ask(TAKE_PROMPT, WORK)
            for i, it in enumerate(items):
                if isinstance(s := raw.get(str(i + 1)), dict) and s.get("take"):
                    it.take = str(s["take"])[:140]
            out["takes"] = sum(bool(it.take) for it in items)
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"[:300]
    out["secs"] = round(time.monotonic() - t)
    return out
