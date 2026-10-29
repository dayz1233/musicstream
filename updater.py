"""Pull only program files whose hash changed. Music, databases, and playlists are never touched."""
import hashlib
import json
import os
import urllib.request
from pathlib import Path

BASE = Path(__file__).parent
VERSION_FILE = BASE / "VERSION"
CONFIG = BASE / "data" / "update.json"
PROTECTED_PREFIXES = (
    "data/",
    "music/",
    "music_hot/",
    "music_cold/",
    "uploads/",
    "__pycache__/",
)


def local_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return "0"


def load_config() -> dict:
    if not CONFIG.exists():
        return {"url": ""}
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"url": ""}


def save_url(url: str) -> None:
    CONFIG.parent.mkdir(exist_ok=True)
    cfg = load_config()
    cfg["url"] = url.strip()
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def allowed(rel: str) -> bool:
    rel = rel.replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return False
    if rel.startswith(PROTECTED_PREFIXES):
        return False
    if rel.endswith(".db"):
        return False
    return True


def _get(url: str, timeout: int):
    request = urllib.request.Request(url, headers={"User-Agent": "MusicStream-Updater"})
    return urllib.request.urlopen(request, timeout=timeout)


def fetch_json(url: str) -> dict:
    with _get(url, 30) as response:
        return json.loads(response.read().decode("utf-8"))


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(dest.suffix + ".download")
    with _get(url, 120) as response, open(temporary, "wb") as handle:
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    os.replace(temporary, dest)


def check_and_apply(url=None, log=print) -> dict:
    config = load_config()
    url = (url or config.get("url") or "").strip()
    if not url:
        return {"ok": False, "reason": "no_url", "changed": []}
    manifest = fetch_json(url)
    remote = str(manifest.get("version", ""))
    local = local_version()
    changed = []
    for item in manifest.get("files") or []:
        rel = str(item.get("path", ""))
        if not allowed(rel):
            log(f"skipped protected path: {rel}")
            continue
        dest = BASE / rel
        want = str(item.get("sha256") or "").lower()
        have = sha256_file(dest) if dest.is_file() else ""
        if want and have == want:
            continue
        file_url = item.get("url")
        if not file_url:
            continue
        log(f"downloading {rel}")
        download(file_url, dest)
        if want and sha256_file(dest).lower() != want:
            raise RuntimeError(f"hash mismatch for {rel}")
        changed.append(rel)
    if remote:
        VERSION_FILE.write_text(remote + "\n", encoding="utf-8")
    return {"ok": True, "local": local, "remote": remote, "changed": changed}
