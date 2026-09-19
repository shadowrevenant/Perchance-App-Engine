"""Pure, dependency-free helpers shared by the Jawless desktop tools."""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from datetime import datetime
from pathlib import Path


BASE_URL = "https://perchance.org"
_SLUG_RE = re.compile(r"^[a-z0-9_-]+$")
_SAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def extract_suffix(text: str) -> str:
    """Return the query, fragment, or extra path after a generator slug."""
    text = (text or "").strip().strip("<>").strip()
    if not text:
        return ""

    markdown_link = re.search(r"\]\(\s*(\S+?)\s*\)", text)
    if markdown_link:
        text = markdown_link.group(1)

    if "://" in text:
        parsed = urllib.parse.urlsplit(text)
        path_parts = [part for part in parsed.path.split("/") if part]
        suffix = ""
        if len(path_parts) > 1:
            suffix += "/" + "/".join(path_parts[1:])
        if parsed.query:
            suffix += "?" + parsed.query
        if parsed.fragment:
            suffix += "#" + parsed.fragment
        return suffix

    if text[0] in "?#/":
        return text
    if text.lower().startswith(("perchance.org/", "www.perchance.org/")):
        text = text.split("/", 1)[1]

    cut_points = [index for index in (text.find("?"), text.find("#")) if index != -1]
    if cut_points:
        return text[min(cut_points):]
    if "/" in text:
        head, _, tail = text.partition("/")
        if _SLUG_RE.fullmatch(head):
            return "/" + tail
    if "=" in text:
        return "?" + text
    return ""


def build_url(slug: str, suffix: str = "") -> str:
    if not _SLUG_RE.fullmatch(slug):
        raise ValueError(f"invalid generator slug: {slug!r}")
    return f"{BASE_URL}/{slug}{suffix or ''}"


def sanitize_filename(name: str, fallback: str = "download") -> str:
    """Strip path separators and Windows-illegal characters."""
    cleaned = urllib.parse.unquote(name or "")
    cleaned = _SAFE_FILENAME_RE.sub("_", cleaned).strip(" .")
    cleaned = cleaned or fallback
    if Path(cleaned).stem.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = "_" + cleaned
    return cleaned[:180]


def unique_path(directory: Path, filename: str, reserved: set[str]) -> Path:
    """Return a non-existing, non-reserved path inside ``directory``."""
    directory = Path(directory)
    stem = Path(filename).stem or "download"
    suffix = Path(filename).suffix
    candidate = f"{stem}{suffix}"
    number = 1
    while (directory / candidate).exists() or candidate.casefold() in reserved:
        candidate = f"{stem} ({number}){suffix}"
        number += 1
        if number > 9999:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            digest = hashlib.sha1(filename.encode()).hexdigest()[:6]
            candidate = f"{stem}_{stamp}_{digest}{suffix}"
            break
    reserved.add(candidate.casefold())
    return directory / candidate


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, expected_size: int, expected_sha256: str) -> tuple[bool, str]:
    """Validate an artifact using exact size and SHA-256."""
    path = Path(path)
    if not path.is_file():
        return False, "file is missing"
    actual_size = path.stat().st_size
    if expected_size and actual_size != expected_size:
        return False, f"size mismatch: expected {expected_size}, got {actual_size}"
    actual_hash = sha256_file(path)
    if expected_sha256 and actual_hash.casefold() != expected_sha256.casefold():
        return False, f"SHA-256 mismatch: expected {expected_sha256}, got {actual_hash}"
    return True, "verified"
