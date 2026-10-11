from dataclasses import dataclass, field, asdict


@dataclass
class Item:
    source: str          # "dribbble", "awwwards", ...
    key: str             # stable id within the source
    title: str
    url: str             # detail page on the source
    image: str
    section: str         # "web" | "ui" | "visual" | "retro"
    author: str = ""
    link: str = ""       # the live site itself, if known
    popularity: float | None = None  # likes, downloads... (None = use position)
    position: int = 0    # order on the source page (0 = top)
    tags: list[str] = field(default_factory=list)
    note: str = ""       # short extra line (e.g. "1998", "SOTD")
    score: float = 0.0   # filled in by ranking

    @property
    def id(self) -> str:
        return f"{self.source}:{self.key}"

    def to_dict(self) -> dict:
        return asdict(self) | {"id": self.id}

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        d = {k: v for k, v in d.items() if k != "id"}
        return cls(**d)
