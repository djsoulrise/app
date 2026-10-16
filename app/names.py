"""Filename/title parsing and normalisation helpers."""
import re

NOISE = {"official", "video", "audio", "lyrics", "lyric", "hd", "hq", "music", "original", "mix",
         "version", "free", "download", "www", "com", "feat", "ft", "prod", "mp3", "clip", "spot"}
VERSION_WORDS = {"mashup", "edit", "remix", "vip", "bootleg", "extended", "intro", "acapella",
                 "acappella", "instrumental", "dub", "radio", "club", "rework", "flip", "live",
                 "cover", "clean", "dirty", "remaster", "remastered", "miljan", "transition",
                 "blend", "bootleg", "refix", "reedit", "mix2"}


def strip_track_no(s: str) -> str:
    return re.sub(r"^\s*\(?\d{1,3}\)?\s*[.\-_)]\s*|^\s*\d{1,3}\s+(?=\D)", "", s).strip()


def split_filename(filename: str, strip_ext: bool = True) -> tuple[str, str]:
    """'01. Artist - Title.mp3' -> ('Artist', 'Title'); artist is '' if not splittable."""
    base = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", filename) if strip_ext else filename
    base = strip_track_no(base.replace("_", " "))
    parts = re.split(r"\s+[-\u2013\u2014]\s+", base, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0].strip(), parts[1].strip()
    return "", base.strip()


def norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE).replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def core_title(artist: str, title: str) -> str:
    """Normalised 'artist title' without bracketed noise, for fuzzy matching."""
    t = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", title or "")
    words = [w for w in norm(f"{artist} {t}").split() if w not in NOISE and not w.isdigit()]
    return " ".join(words)


def version_signature(title: str) -> tuple:
    words = set(norm(title).split())
    return tuple(sorted(words & VERSION_WORDS))


def song_key(artist: str, title: str) -> str:
    return norm(f"{artist} {title}")
