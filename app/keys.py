"""Open Key / Camelot / note-name key conversion."""
import re

_PC = {"c": 0, "c#": 1, "db": 1, "d": 2, "d#": 3, "eb": 3, "e": 4, "f": 5, "f#": 6,
       "gb": 6, "g": 7, "g#": 8, "ab": 8, "a": 9, "a#": 10, "bb": 10, "b": 11}


def _open_from_pc(pc: int, minor: bool) -> int:
    major_pc = (pc + 3) % 12 if minor else pc
    return (major_pc * 7) % 12 + 1


def open_to_camelot(open_key: str) -> str:
    n, mode = int(open_key[:-1]), open_key[-1]
    return f"{(n + 6) % 12 + 1}{'B' if mode == 'd' else 'A'}"


def parse_key(s) -> tuple[str, str] | None:
    """Return (open_key, camelot) for Open Key (1m), Camelot (8A) or note (Am, F#) input."""
    if not s:
        return None
    s = str(s).strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})([dm])", s)
    if m and 1 <= int(m[1]) <= 12:
        ok = f"{int(m[1])}{m[2]}"
        return ok, open_to_camelot(ok)
    m = re.fullmatch(r"(\d{1,2})([ab])", s)
    if m and 1 <= int(m[1]) <= 12:
        num = (int(m[1]) - 7) % 12 or 12
        ok = f"{num}{'d' if m[2] == 'b' else 'm'}"
        return ok, open_to_camelot(ok)
    m = re.fullmatch(r"([a-g](?:#|b)?)(m|min|minor|maj|major)?", s)
    if m and m[1] in _PC:
        minor = (m[2] or "") in ("m", "min", "minor")
        ok = f"{_open_from_pc(_PC[m[1]], minor)}{'m' if minor else 'd'}"
        return ok, open_to_camelot(ok)
    return None


def from_musical_value(v) -> tuple[str, str] | None:
    """Traktor MUSICAL_KEY VALUE: 0-11 major C..B, 12-23 minor c..b."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    if not 0 <= v <= 23:
        return None
    ok = f"{_open_from_pc(v % 12, v >= 12)}{'m' if v >= 12 else 'd'}"
    return ok, open_to_camelot(ok)


def _split(c: str) -> tuple[int, str]:
    return int(c[:-1]), c[-1]


def key_score(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.4
    (na, la), (nb, lb) = _split(a), _split(b)
    d = min((na - nb) % 12, (nb - na) % 12)
    if la == lb:
        return {0: 1.0, 1: 0.9, 2: 0.55, 3: 0.2}.get(d, 0.0)
    return {0: 0.9, 1: 0.45}.get(d, 0.0)


def key_relation(a: str | None, b: str | None) -> str:
    if not a or not b:
        return "key nepoznat"
    (na, la), (nb, lb) = _split(a), _split(b)
    d = min((na - nb) % 12, (nb - na) % 12)
    if a == b:
        return f"isti key ({b})"
    if la != lb and d == 0:
        return f"relativni key ({a} -> {b})"
    if la == lb and d == 1:
        return f"susedni key ({a} -> {b})"
    if la == lb and d == 2:
        return f"key +/-2 ({a} -> {b}), podize energiju"
    return f"key {a} -> {b} (slabije se slaze)"
