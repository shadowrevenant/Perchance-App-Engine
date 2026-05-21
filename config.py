"""
config.py — Shared paths and constants for Perchance App Engine
"""
import sys
from pathlib import Path

APP_ROOT   = Path(__file__).parent.resolve()
GENS_DIR   = APP_ROOT / "gens"
ASSETS_DIR = APP_ROOT / "assets"

# Per-generator data lives under data/<slug>/
DATA_DIR   = APP_ROOT / "data"
# data/<slug>/cache/   — HTTP cache (unlimited, never cleared by OS)
# data/<slug>/storage/ — localStorage, IndexedDB, cookies
# data/<slug>/files/   — user file downloads / saves

GLOBAL_JS  = APP_ROOT / "global-overrides.js"
APP_RUNNER = APP_ROOT / "app_runner" / "runner.py"

GENS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
ASSETS_DIR.mkdir(exist_ok=True)


def gen_dir(slug: str) -> Path:
    return GENS_DIR / slug

def gen_data_dir(slug: str) -> Path:
    d = DATA_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    return d

def gen_cache_dir(slug: str) -> Path:
    d = gen_data_dir(slug) / "cache"
    d.mkdir(exist_ok=True)
    return d

def gen_storage_dir(slug: str) -> Path:
    d = gen_data_dir(slug) / "storage"
    d.mkdir(exist_ok=True)
    return d

def gen_files_dir(slug: str) -> Path:
    d = gen_data_dir(slug) / "files"
    d.mkdir(exist_ok=True)
    return d

def list_generators():
    if not GENS_DIR.exists():
        return []
    return sorted(
        d.name for d in GENS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )

def read_meta(slug: str) -> dict:
    """Read generator metadata from meta.json (with fallbacks)."""
    import json
    meta_file = gen_dir(slug) / "meta.json"
    defaults = {
        "name": slug,
        "slug": slug,
        "description": "",
        "url": f"https://perchance.org/{slug}",
        "color": "#01696f",
        "version": "1.0",
    }
    if meta_file.exists():
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            defaults.update(data)
        except Exception:
            pass
    return defaults

def write_meta(slug: str, meta: dict):
    import json
    meta_file = gen_dir(slug) / "meta.json"
    meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")