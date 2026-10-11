"""One fetcher per source. Each returns a list of Items, or raises.

Fetchers are isolated: one broken source never breaks the digest.
"""
import json
import random
import re
from datetime import date
from urllib.parse import urljoin

import feedparser
import httpx
from selectolax.lexbor import LexborHTMLParser

from .models import Item

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
        timeout=30,
        follow_redirects=True,
        transport=httpx.AsyncHTTPTransport(retries=2),  # retries connect errors/timeouts
    )


async def get(c: httpx.AsyncClient, url: str) -> str:
    r = await c.get(url)
    if r.status_code in (403, 429, 503):
        # Cloudflare often blocks datacenter IPs (GitHub Actions) for plain HTTP
        return await browser_get(url)
    r.raise_for_status()
    return r.text


async def browser_get(url: str) -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=UA, viewport={"width": 1440, "height": 1200})
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)  # let a JS challenge settle
        html = await page.content()
        status = resp.status if resp else 0
        await browser.close()
    if status >= 400 and "<article" not in html and "website-item" not in html:
        raise httpx.HTTPStatusError(f"{status} via browser for {url}", request=None, response=None)
    return html


async def browser_rows(url: str, js: str, wait_for: str | None = None) -> list[dict]:
    """Open a JS-rendered page, scroll a bit, and run an extractor in it."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=UA, viewport={"width": 1440, "height": 1400})
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if wait_for:
            await page.wait_for_selector(wait_for, timeout=30000)
        await page.wait_for_timeout(4000)
        await page.mouse.wheel(0, 3000)
        await page.wait_for_timeout(1500)
        rows = await page.evaluate(js)
        await browser.close()
    if not rows:
        raise RuntimeError(f"nothing extracted from {url}")
    return rows


def text(node) -> str:
    return node.text(strip=True) if node else ""


# --- Web ---------------------------------------------------------------------

async def awwwards(c):
    base = "https://www.awwwards.com"
    tree = LexborHTMLParser(await get(c, base + "/websites/sites_of_the_day/"))
    items = []
    for i, card in enumerate(tree.css(".card-site")):
        a = card.css_first("a.figure-rollover__link")
        img = card.css_first("img[data-srcset]")
        if not a or not img:
            continue
        image = img.attributes["data-srcset"].split(" ")[0].replace("thumb_440_330", "thumb_880_660")
        visit = card.css_first("a.figure-rollover__bt")
        awards = [text(t) for t in card.css(".budget-tag")]
        items.append(Item(
            source="awwwards", key=a.attributes["href"].strip("/").split("/")[-1],
            title=a.attributes.get("aria-label", ""), url=urljoin(base, a.attributes["href"]),
            image=image, section="web", author=text(card.css_first(".avatar-name__title")),
            link=visit.attributes.get("href", "") if visit else "", position=i,
            tags=awards, note=" · ".join(awards),
        ))
    return items


async def landbook(c):
    base = "https://land-book.com"
    tree = LexborHTMLParser(await get(c, base))
    items, seen = [], set()
    for a in tree.css("a.website-item-link"):
        href = a.attributes.get("href", "")
        img = a.css_first("img")
        if not img or href in seen:
            continue
        seen.add(href)
        title = img.attributes.get("alt", "").removesuffix(" - Landing design inspiration")
        title = re.sub(r"\s+-\s+\w+ design inspiration$", "", title)
        items.append(Item(
            source="landbook", key=href.split("/")[-1].split("-")[0], title=title,
            url=urljoin(base, href), image=img.attributes["src"], section="web",
            position=len(items),
        ))
    return items


def _feed(source: str, url: str, section: str):
    async def fetch(c):
        feed = feedparser.parse(await get(c, url))
        items = []
        for i, e in enumerate(feed.entries):
            html = (e.get("content") or [{}])[0].get("value", "") + e.get("summary", "")
            m = re.search(r'<img[^>]+src="([^"]+)"', html)
            if not m:
                continue
            items.append(Item(
                source=source, key=e.get("id", e.link),
                title=re.sub(r"^Website Inspiration:\s*", "", e.title), url=e.link,
                image=m.group(1), section=section, position=i,
                tags=[t.term for t in e.get("tags", [])][:4],
            ))
        return items
    return fetch


minimal_gallery = _feed("minimalgallery", "https://minimal.gallery/feed/", "web")
onepagelove = _feed("onepagelove", "https://onepagelove.com/feed", "web")


# --- Visual & UI -------------------------------------------------------------

async def godly(c):
    base = "https://godly.website"
    tree = LexborHTMLParser(await get(c, base))
    items = []
    for art in tree.css("article"):
        a = art.css_first('a[aria-label^="View post:"]')
        img = art.css_first("img")
        if not a or not img or "/items/" not in img.attributes.get("src", ""):
            continue
        href = a.attributes["href"]
        items.append(Item(
            source="godly", key=href.split("/")[-1].split("-")[0],
            title=a.attributes["aria-label"].removeprefix("View post:").strip(),
            url=urljoin(base, href), image=img.attributes["src"].replace("/1200.", "/960."),
            section="visual", position=len(items),
        ))
    return items


# Visual categories on 21st.dev; a few rotate in each day.
TWENTYFIRST_CATEGORIES = [
    "hero", "shader", "background", "features", "pricing", "testimonials", "cta",
    "text", "card", "navigation-menu", "footer", "carousel", "ai-chat", "cursor",
]


async def twentyfirst(c):
    """21st.dev components by category — data sits in the Next.js RSC payload."""
    day = date.today().toordinal()
    cats = [TWENTYFIRST_CATEGORIES[(day * 3 + i) % len(TWENTYFIRST_CATEGORIES)] for i in range(3)]
    items, seen = [], set()
    for cat in cats:
        try:
            html = await get(c, f"https://21st.dev/community/components/s/{cat}")
        except httpx.HTTPError:
            continue
        items += _twentyfirst_parse(html, cat, seen)
    if not items:
        raise RuntimeError(f"no components parsed from {cats}")
    return items


def _twentyfirst_parse(html: str, category: str, seen: set) -> list[Item]:
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html)
    payload = "".join(json.loads(f'"{ch}"') for ch in chunks)
    dec = json.JSONDecoder()
    items = []
    for m in re.finditer(r'"component_data":', payload):
        try:
            comp, end = dec.raw_decode(payload, m.end())
        except ValueError:
            continue
        # source_url is often null; the author's username follows in "user_data"
        user = re.match(r',"user_data":\{[^}]*?"username":"([^"]+)"', payload[end:end + 400])
        url = comp.get("source_url") or (
            f"https://21st.dev/{user.group(1)}/{comp['component_slug']}" if user and comp.get("component_slug") else ""
        )
        if not url or url in seen or not comp.get("preview_url"):
            continue
        seen.add(url)
        likes, downloads = comp.get("likes_count") or 0, comp.get("downloads_count") or 0
        items.append(Item(
            source="21st", key=url.removeprefix("https://21st.dev/"), title=comp.get("name", ""),
            url=url, image=comp["preview_url"], section="ui",
            author=url.split("/")[3] if url.count("/") >= 4 else "",
            popularity=likes * 10 + downloads, position=len(items),
            tags=[category], note=f"{category} · ↓ {downloads}",
        ))
    # percentile inside the category, so one busy category doesn't take every slot
    for rank, it in enumerate(sorted(items, key=lambda it: it.popularity, reverse=True)):
        it.popularity = 1 - rank / max(len(items) - 1, 1)
    return items


async def dribbble(_c):
    """Dribbble sits behind a bot check — needs a real browser."""
    rows = await browser_rows("https://dribbble.com/shots/popular", wait_for="li.shot-thumbnail", js="""() =>
        [...document.querySelectorAll('li.shot-thumbnail')].map(li => ({
            href: li.querySelector('a.shot-thumbnail-link')?.getAttribute('href'),
            title: li.querySelector('a.shot-thumbnail-link .accessibility-text')?.textContent.trim(),
            // lazy-loaded thumbs carry a 1px placeholder in src
            img: (() => {
                const im = li.querySelector('figure img');
                const cands = [im?.getAttribute('src'), im?.dataset.src, im?.getAttribute('srcset')?.split(' ')[0],
                    li.querySelector('figure noscript')?.textContent.match(/src="([^"]+)"/)?.[1]];
                return cands.find(u => u && u.startsWith('https://')) || null;
            })(),
            author: li.querySelector('.display-name')?.textContent.trim(),
            likes: li.querySelector('[data-shot-like-count]')?.textContent.trim(),
            views: li.querySelector('.js-shot-views-count')?.textContent.trim(),
        }))""")

    def num(s):
        s = (s or "0").strip().lower().replace(",", "")
        mult = 1000 if s.endswith("k") else 1_000_000 if s.endswith("m") else 1
        try:
            return float(s.rstrip("km")) * mult
        except ValueError:
            return 0.0

    items = []
    for r in rows:
        if not r["href"] or not r["img"]:
            continue
        image = re.sub(r"resize=\d+x\d+", "resize=800x600", r["img"])
        items.append(Item(
            source="dribbble", key=r["href"].split("/")[-1].split("-")[0],
            title=(r["title"] or "").removeprefix("View ").strip(),
            url=urljoin("https://dribbble.com", r["href"]), image=image, section="visual",
            author=r["author"] or "", popularity=num(r["likes"]), position=len(items),
            note=f"♥ {r['likes']} · 👁 {r['views']}",
        ))
    return items


COSMOS_CATEGORIES = {"design", "art", "ideas", "photography", "architecture", "typography"}


async def cosmos(_c):
    """Cosmos 'discover' — curated visual clusters (art, posters, moods)."""
    rows = await browser_rows("https://cosmos.so/discover", """() =>
        [...document.querySelectorAll('a[href]')].map(a => {
            const im = a.querySelector('img');
            return im && { href: a.href, img: im.currentSrc || im.src };
        }).filter(r => r && r.href.includes('cosmos.so/') && r.img.startsWith('https://cdn.cosmos.so/'))""")
    items, seen = [], set()
    for r in rows:
        path = r["href"].split("cosmos.so/")[-1].strip("/")
        if path in seen or path.count("/") != 1:
            continue
        seen.add(path)
        category, slug = path.split("/")
        if category not in COSMOS_CATEGORIES:  # skip fashion, food, interiors…
            continue
        image = re.sub(r"([?&])w=\d+", r"\1w=800", r["img"])
        items.append(Item(
            source="cosmos", key=path, title=slug.replace("-", " ").capitalize(), url=r["href"],
            image=image, section="visual", note=category, position=len(items),
        ))
    return items


# --- WOW / experimental -----------------------------------------------------

async def codrops(c):
    """Codrops Hub — WebGL, Three.js, shader and interaction demos."""
    tree = LexborHTMLParser(await get(c, "https://tympanus.net/codrops/hub/"))
    items = []
    for art in tree.css("article.demo-card"):
        a, h2 = art.css_first("a.demo-card__thumb-img"), art.css_first("h2")
        video, img = art.css_first("video"), art.css_first("img")
        image = (video.attributes.get("poster") if video else None) or (img.attributes.get("src") if img else None)
        if not a or not h2 or not image:
            continue
        tags = [text(t) for t in art.css(".demo-card__tags a")][:4]
        article = art.css_first("a.icon--article")
        items.append(Item(
            source="codrops", key=art.attributes.get("id", a.attributes["href"]), title=text(h2),
            url=article.attributes["href"] if article else a.attributes["href"],
            link=a.attributes["href"], image=image.split("?")[0], section="wow",
            author=text(art.css_first(".demo-card__author")), position=len(items),
            tags=tags, note=" · ".join(tags[:2]),
        ))
    return items


async def gsap_showcase(_c):
    """GSAP showcase — sites built around ambitious motion."""
    rows = await browser_rows("https://gsap.com/showcase/", """() =>
        [...document.querySelectorAll('a[href]')].map(a => {
            const im = a.querySelector('img');
            return im && { href: a.href, img: im.currentSrc || im.src, title: (im.alt || a.innerText || '').trim() };
        }).filter(r => r && r.img.includes('/community/uploads/') && !r.href.includes('gsap.com'))""")
    items, seen = [], set()
    for r in rows:
        if r["href"] in seen:
            continue
        seen.add(r["href"])
        items.append(Item(
            source="gsap", key=r["href"], title=r["title"].split("\n")[0][:80] or r["href"],
            url=r["href"], link=r["href"], image=r["img"], section="wow", position=len(items),
            note="motion",
        ))
    return items


async def brutalist(c):
    """Brutalist Websites — ~1600 raw, odd, opinionated sites. A random handful daily."""
    base = "https://brutalistwebsites.com"
    tree = LexborHTMLParser(await get(c, base))
    items, seen = [], set()
    for a in tree.css("a[href]"):
        img = a.css_first("img")
        href = a.attributes.get("href", "")
        if not img or "/_img/" not in img.attributes.get("src", "") or href in seen or "brutalistwebsites.com" in href:
            continue
        seen.add(href)
        domain = re.sub(r"^https?://(www\.)?", "", href).strip("/")
        items.append(Item(
            source="brutalist", key=domain, title=domain, url=href, link=href,
            image=urljoin(base + "/", img.attributes["src"]).replace("http://", "https://"), section="wow",
            note="brutalist", position=random.randint(0, 10_000),
        ))
    return items


# --- More web galleries ------------------------------------------------------

async def cssda(c):
    base = "https://www.cssdesignawards.com/"
    tree = LexborHTMLParser(await get(c, base))
    items = []
    for art in tree.css("article.single-project"):
        a, img = art.css_first(".single-project__title a"), art.css_first(".single-project__thumbnail img")
        if not a or not img:
            continue
        visit = art.css_first("a.sp__project-link")
        badge = text(art.css_first(".sp__meta__category"))
        items.append(Item(
            source="cssda", key=a.attributes["href"].strip("/").split("/")[-1], title=text(a),
            url=urljoin(base, a.attributes["href"]), image=urljoin(base, img.attributes["src"]),
            link=visit.attributes.get("href", "") if visit else "", section="web",
            position=len(items), note=badge.title(),
        ))
    return items


async def webflow(c):
    base = "https://webflow.com"
    tree = LexborHTMLParser(await get(c, base + "/made-in-webflow/popular"))
    items, seen = [], set()
    for a in tree.css('a[href*="/made-in-webflow/website/"]'):
        img = a.css_first("img")
        href = a.attributes["href"]
        if not img or href in seen:
            continue
        seen.add(href)
        items.append(Item(
            source="webflow", key=href.rstrip("/").split("/")[-1],
            title=img.attributes.get("alt", "").removesuffix(" thumbnail"),
            url=urljoin(base, href), image=img.attributes["src"], section="web", position=len(items),
        ))
    return items


async def darkdesign(c):
    base = "https://dark.design"
    tree = LexborHTMLParser(await get(c, base))
    items, seen = [], set()
    for a in tree.css('a[href^="/site/"]'):
        img = a.css_first("img")
        href = a.attributes["href"]
        if not img or href in seen:
            continue
        seen.add(href)
        items.append(Item(
            source="darkdesign", key=href.split("/")[-1], title=img.attributes.get("alt", ""),
            url=urljoin(base, href), image=img.attributes["src"], section="web",
            position=len(items), note="dark UI",
        ))
    return items


async def bestwebsite(c):
    base = "https://bestwebsite.gallery"
    tree = LexborHTMLParser(await get(c, base))
    items = []
    for art in tree.css("article"):
        # the featured card has an <h1> title; the grid cards only an aria-label
        a = art.css_first('h1 a[href*="/sites/sotd/"]') or art.css_first('a[aria-label][href*="/sites/sotd/"]:not([aria-label^="View"])')
        img = art.css_first("figure img")
        if not a or not img:
            continue
        external = art.css_first('a[aria-label^="Open"][target="_blank"]')
        author = art.css_first('a[href*="/sites/creator/"]')
        items.append(Item(
            source="bestwebsite", key=a.attributes["href"].strip("/"),
            title=text(a) or a.attributes.get("aria-label", ""),
            url=urljoin(base, a.attributes["href"]), image=img.attributes["src"], section="web",
            link=external.attributes["href"] if external else "", author=text(author), position=len(items),
        ))
    return items


# --- Retro -------------------------------------------------------------------

async def webdesignmuseum(c):
    """Old sites (1991–2000s) + web-design history milestones, shuffled.

    Paginated gallery pages sit behind Cloudflare, so we use the long
    single pages: gallery front, the 90s exhibition and the history timeline.
    """
    base = "https://www.webdesignmuseum.org"
    items = []
    for path in ("/gallery", "/web-design-in-the-90s","/web-design-history"):
        try:
            html = await get(c, base + path)
        except httpx.HTTPError:
            continue
        for art in LexborHTMLParser(html).css("article"):
            img, h2 = art.css_first("img"), art.css_first("h2")
            a = art.css_first("h3 a") or art.css_first("a")
            if not a or not img or not h2:
                continue
            small = text(h2.css_first("small"))
            if "timeline" in art.attributes.get("class", ""):
                year = re.search(r"\d{4}", text(h2))
                title, note = text(a), f"{year.group() if year else ''} · história webu".strip(" ·")
            else:
                title, note = text(h2).removesuffix(small).strip(), small
            items.append(Item(
                source="webdesignmuseum", key=a.attributes["href"].rstrip("/").split("/")[-1],
                title=title, url=a.attributes["href"], image=img.attributes["src"],
                section="retro", note=note, position=random.randint(0, 10_000),
            ))
    if not items:
        raise RuntimeError("no items parsed (blocked?)")
    return list({it.key: it for it in items}.values())


SOURCES = {
    "awwwards": awwwards,
    "landbook": landbook,
    "minimalgallery": minimal_gallery,
    "onepagelove": onepagelove,
    "cssda": cssda,
    "webflow": webflow,
    "darkdesign": darkdesign,
    "bestwebsite": bestwebsite,
    "godly": godly,
    "dribbble": dribbble,
    "cosmos": cosmos,
    "codrops": codrops,
    "gsap": gsap_showcase,
    "brutalist": brutalist,
    "21st": twentyfirst,
    "webdesignmuseum": webdesignmuseum,
}

LABELS = {
    "awwwards": "Awwwards",
    "landbook": "Land-book",
    "minimalgallery": "Minimal Gallery",
    "onepagelove": "One Page Love",
    "cssda": "CSS Design Awards",
    "webflow": "Made in Webflow",
    "darkdesign": "Dark.design",
    "bestwebsite": "Best Website Gallery",
    "godly": "Godly",
    "cosmos": "Cosmos",
    "codrops": "Codrops",
    "gsap": "GSAP Showcase",
    "brutalist": "Brutalist Websites",
    "21st": "21st.dev",
    "dribbble": "Dribbble",
    "webdesignmuseum": "Web Design Museum",
}
