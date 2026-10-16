import glob
import json
import os
import secrets
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
_default_data = (Path(os.environ.get("APPDATA", str(Path.home()))) / "DJ asistent") if getattr(sys, "frozen", False) else BASE / "data"
DATA = Path(os.environ.get("DJ_DATA_DIR") or _default_data)
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "uploads").mkdir(exist_ok=True)
SETTINGS_FILE = DATA / "settings.json"

DEFAULT_WEIGHTS = {
    "hist": 3.0, "key": 1.5, "bpm": 1.5, "vibe": 1.5,
    "energy": 1.0, "year": 0.3, "genre": 0.3, "feedback": 0.5, "style": 2.0,
}

DEFAULTS = {
    "music_folders": [],
    "nml_path": "",
    "history_dir": "",
    "dupes_dir": "",
    "export_dir": "",
    "main_folder_hint": "muzikaa",
    "host": "0.0.0.0",
    "port": 8765,
    "broadcast_enabled": True,
    "broadcast_port": 8000,
    "dj_pin": "",
    "anthropic_key": "",
    "model": "claude-sonnet-4-5",
    "weights": DEFAULT_WEIGHTS,
    "mode": "ravno",
    "style_strictness": "normalno",
    "guest_limit": {"count": 2, "minutes": 30},
    "tip_text": "",
    "tip_ips_payload": "",
    "tip_image": "",
    "audience": {
        "publika": "mesovita, svadbe i rodjendani",
        "domace_strano": "50/50",
        "zanrovi": "narodno, folk, pop, house, 80s/90s hitovi",
        "zabranjeno": "teski techno, trap",
        "godine": "1980-danas",
        "cista_verzija": True,
    },
}


def _docs() -> Path:
    return Path(os.path.expanduser("~")) / "Documents"


def detect_nml() -> str:
    hits = glob.glob(str(_docs() / "Native Instruments" / "Traktor *" / "collection.nml"))
    return max(hits, key=os.path.getmtime) if hits else ""


def detect_history_dir() -> str:
    hits = [h for h in glob.glob(str(_docs() / "Native Instruments" / "Traktor *" / "History")) if os.path.isdir(h)]
    return max(hits, key=os.path.getmtime) if hits else ""


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except (OSError, ValueError):
            pass
    first = not SETTINGS_FILE.exists()
    if not cfg["dj_pin"]:
        cfg["dj_pin"] = f"{secrets.randbelow(10**6):06d}"
    if first:
        cfg["nml_path"] = cfg["nml_path"] or detect_nml()
        cfg["history_dir"] = cfg["history_dir"] or detect_history_dir()
        muz = _docs() / "Muzikaa"
        if muz.is_dir() and not cfg["music_folders"]:
            cfg["music_folders"] = [str(muz)]
        cfg["dupes_dir"] = cfg["dupes_dir"] or str(_docs() / "Duplikati")
        cfg["export_dir"] = cfg["export_dir"] or str(_docs() / "DJ asistent plejliste")
        save(cfg)
    return cfg


def save(cfg: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


SETTINGS = load()
