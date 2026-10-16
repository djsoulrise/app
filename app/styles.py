"""Style (vibe family) engine. Each track gets a weighted style vector from folder names, tags, genre and title;
suggestions are hard-gated by style compatibility so e.g. folk never follows a house track (history is ignored then)."""
import re
from collections import defaultdict

FAMILIES = ["house", "narodno", "domace_pop", "domaci_rock", "rock", "rnb_hiphop", "pop_dance", "disco",
            "latino", "world", "lounge", "kids"]
LABELS = {"house": "house/electronic", "narodno": "narodno/folk", "domace_pop": "domaca zabavna/pop",
          "domaci_rock": "domaci rock", "rock": "rock", "rnb_hiphop": "rap/R&B", "pop_dance": "pop/dance hitovi",
          "disco": "80s/disco/funk", "latino": "latino", "world": "ruska/grcka/arab", "lounge": "lounge/chill",
          "kids": "deca"}

# (family, regex, weight, sources) ; sources: f=folder, g=genre, t=tag, l=title
RULES = [
    ("house", r"house|techno|trance|electro|\bpsy|\bhard\b|deep|afro|melodic|funkymove|\bedm\b|progressive|\bclub\b", 1.0, "ft"),
    ("house", r"house|techno|trance|electro|\bpsy|deep|afro|melodic|\bedm\b|progressive", 2.5, "g"),
    ("house", r"sunset|^after", 0.7, "ft"),
    ("narodno", r"narodno|seljana|\bfolk|\bkola\b|trubac|balkan|kafana|etno|teske|turbo", 1.5, "fgt"),
    ("narodno", r"domac.*zurka|zurka.*domac", 0.8, "ft"),
    ("domace_pop", r"domac|retro vece|start domace", 0.6, "ft"),
    ("domace_pop", r"pop.?folk|zabavna", 0.8, "fgt"),
    ("narodno", r"pop.?folk", 0.6, "fgt"),
    ("domaci_rock", r"rock.*domac|domac.*rock|kluboslavija|ex ?yu", 1.2, "ft"),
    ("rock", r"\brock|altern|metal|punk", 1.0, "fgt"),
    ("rnb_hiphop", r"\brap\b|hip.?hop|r&b|\brnb\b|\bsoul", 1.0, "fgt"),
    ("pop_dance", r"eurodance|\bdance\b|\b90s\b|\b00s\b|2000s strane|goldie|top 40|^hits$", 1.0, "fgt"),
    ("pop_dance", r"party hits", 0.8, "ft"),
    ("pop_dance", r"\bparty\b", 0.4, "ft"),
    ("pop_dance", r"^pop$|^pop,|, pop|pop ingl", 1.0, "g"),
    ("pop_dance", r"^pop$", 0.8, "t"),
    ("pop_dance", r"mashup", 0.5, "ft"),
    ("disco", r"\b80s\b|disco|funk(?!ymove)|sixties|swingin|bombs", 1.0, "fgt"),
    ("latino", r"latin|salsa|reggaeton|regeton|italiano|bachata", 1.0, "fgt"),
    ("world", r"rusk|grcke|arab|\bworld\b", 1.0, "fgt"),
    ("lounge", r"lounge|jazz|chill|blues", 0.8, "fgt"),
    ("pop_dance", r"start lagano", 0.5, "t"),
    ("lounge", r"start lagano", 0.5, "t"),
    ("house", r"start house", 1.0, "t"),
    ("kids", r"\bkids\b|kidz|deca", 1.0, "fgt"),
]
_COMPILED = [(f, re.compile(p), w, s) for f, p, w, s in RULES]
_GENERIC_DIRS = {"users", "documents", "muzikaa", "music", "top lista", "liste", "setovi", "downloads"}


def _pairs(spec: str, val: float, out: dict) -> None:
    for line in spec.strip().splitlines():
        a, bs = line.split(":")
        for b in bs.split():
            out[frozenset((a.strip(), b))] = val


_M: dict[frozenset, float] = {}
_pairs("house: pop_dance lounge", 0.65, _M)
_pairs("house: latino", 0.6, _M)
_pairs("house: disco world", 0.5, _M)
_pairs("house: rnb_hiphop", 0.4, _M)
_pairs("house: rock kids", 0.15, _M)
_pairs("house: narodno domaci_rock", 0.05, _M)
_pairs("house: domace_pop", 0.1, _M)
_pairs("narodno: domace_pop", 0.8, _M)
_pairs("narodno: world", 0.55, _M)
_pairs("narodno: domaci_rock", 0.4, _M)
_pairs("narodno: pop_dance", 0.2, _M)
_pairs("narodno: latino disco rock kids lounge", 0.1, _M)
_pairs("narodno: rnb_hiphop", 0.08, _M)
_pairs("domace_pop: pop_dance", 0.4, _M)
_pairs("domace_pop: domaci_rock", 0.65, _M)
_pairs("domace_pop: rnb_hiphop world", 0.4, _M)
_pairs("domace_pop: rock latino disco", 0.25, _M)
_pairs("domace_pop: lounge kids", 0.15, _M)
_pairs("domaci_rock: rock", 0.85, _M)
_pairs("domaci_rock: pop_dance disco", 0.35, _M)
_pairs("domaci_rock: latino rnb_hiphop lounge kids world", 0.1, _M)
_pairs("rock: pop_dance disco", 0.5, _M)
_pairs("rock: rnb_hiphop latino lounge world", 0.2, _M)
_pairs("rock: kids", 0.15, _M)
_pairs("rnb_hiphop: pop_dance disco", 0.65, _M)
_pairs("rnb_hiphop: latino lounge", 0.45, _M)
_pairs("rnb_hiphop: world kids", 0.2, _M)
_pairs("pop_dance: disco latino", 0.8, _M)
_pairs("pop_dance: world", 0.4, _M)
_pairs("pop_dance: lounge", 0.4, _M)
_pairs("pop_dance: kids", 0.5, _M)
_pairs("disco: latino", 0.6, _M)
_pairs("disco: lounge", 0.45, _M)
_pairs("disco: world kids", 0.25, _M)
_pairs("latino: world", 0.5, _M)
_pairs("latino: lounge", 0.4, _M)
_pairs("latino: kids", 0.2, _M)
_pairs("world: lounge", 0.35, _M)
_pairs("world: kids", 0.1, _M)
_pairs("lounge: kids", 0.1, _M)


def pair_compat(a: str, b: str) -> float:
    return 1.0 if a == b else _M.get(frozenset((a, b)), 0.2)


def _folder_segments(path: str) -> list[str]:
    segs = [s for s in path.lower().replace("\\", "/").split("/")[:-1] if s and s not in _GENERIC_DIRS and not s.endswith(":")]
    return segs[-4:]


def style_vector(path: str, genre: str, tags: set[str], title: str, override: str | None = None) -> dict[str, float]:
    if override in FAMILIES:
        return {override: 1.0}
    sc: dict[str, float] = defaultdict(float)
    folder_sc: dict[str, float] = defaultdict(float)
    segs = _folder_segments(path or "")
    for i, seg in enumerate(segs):
        depth_w = 1.5 if i == len(segs) - 1 else (0.7 if i == len(segs) - 2 else 0.4)
        for fam, rx, w, src in _COMPILED:
            if "f" in src and rx.search(seg):
                folder_sc[fam] += w * depth_w
    for fam, v in folder_sc.items():
        sc[fam] += min(v, 2.0)
    for g in re.split(r"[;/]", (genre or "").lower()):
        g = g.strip()
        if g:
            for fam, rx, w, src in _COMPILED:
                if "g" in src and rx.search(g):
                    sc[fam] += w
    for t in tags:
        for fam, rx, w, src in _COMPILED:
            if "t" in src and rx.search(t):
                sc[fam] += w * 1.2
    tl = (title or "").lower()
    for fam, rx, w, src in _COMPILED:
        if "l" in src and rx.search(tl):
            sc[fam] += w * 0.3
    total = sum(sc.values())
    return {k: v / total for k, v in sc.items()} if total > 0 else {}


def compat(a: dict[str, float], b: dict[str, float]) -> float | None:
    """0..1 style compatibility; None if either track has no style signal."""
    if not a or not b:
        return None
    return sum(wa * wb * pair_compat(fa, fb) for fa, wa in a.items() for fb, wb in b.items())


def primary(v: dict[str, float]) -> str | None:
    return max(v, key=v.get) if v else None


def describe(v: dict[str, float]) -> str:
    return ", ".join(f"{LABELS[k]} {round(w * 100)}%" for k, w in sorted(v.items(), key=lambda x: -x[1])[:3]) or "nepoznat"


STRICTNESS = {"strogo": 0.5, "normalno": 0.3, "slobodno": 0.0}
