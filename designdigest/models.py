import re
from dataclasses import dataclass, field, asdict
from urllib.parse import urlsplit

# Titles too generic to say two gallery entries are the same site.
GENERIC_TITLES = {"portfolio", "homepage", "home", "website", "landingpage", "agency", "studio", "untitled"}


@dataclass
class Item:
    source: str          # "dribbble", "awwwards", ...
    key: str             # stable id within the source
    title: str
    url: str             # detail page on the source
    image: str
    section: str         # a key of rank.SECTIONS
    author: str = ""
    link: str = ""       # the live site itself, if known
    popularity: float | None = None  # likes, downloads... (None = use position)
    position: int = 0    # order on the source page (0 = top)
    tags: list[str] = field(default_factory=list)
    note: str = ""       # short extra line (e.g. "1998", "SOTD")
    score: float = 0.0   # filled in by ranking
    why: str = ""        # AI curator's one-line take
    take: str = ""       # what's worth stealing, written after the live site was analysed
    fonts: list[str] = field(default_factory=list)    # typefaces, most prominent first
    palette: list[str] = field(default_factory=list)  # hex colours, most used first
    tech: list[str] = field(default_factory=list)     # GSAP, Three.js, Webflow…

    image_orig: str = ""  # where the image came from, before we mirrored it

    @property
    def id(self) -> str:
        return f"{self.source}:{self.key}"

    @property
    def site(self) -> str:
        """The live site as host + path ("petrillo.studio", "tympanus.net/development/x"), or ""."""
        if not self.link:
            return ""
        u = urlsplit(self.link.lower())
        path = re.sub(r"/(index\.html?)?$", "", u.path)
        return u.netloc.removeprefix("www.") + path

    def dedupe_keys(self) -> set[str]:
        """Same thing showcased by several galleries shares one of these."""
        keys = {f"site:{self.site}"} if self.site else set()
        if self.section in ("web", "wow"):
            name = re.sub(r"[^a-z0-9]", "", self.title.lower().split(" | ")[0].split(" - ")[0])
            if len(name) >= 5 and name not in GENERIC_TITLES:
                keys.add(f"title:{name}")
        return keys

    def to_dict(self) -> dict:
        return asdict(self) | {"id": self.id}

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        d = {k: v for k, v in d.items() if k != "id"}
        return cls(**d)
