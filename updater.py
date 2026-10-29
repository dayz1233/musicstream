"""Pull only program files whose hash changed. Music, databases, and playlists are never touched."""
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).parent
VERSION_FILE = BASE / "VERSION"
CONFIG = BASE / "data" / "update.json"
APPLIED = BASE / "data" / "applied_version"
DEFAULT_UPDATE_URL = "https://raw.githubusercontent.com/dayz1233/musicstream/main/update-manifest.json"
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
    cfg = {"url": DEFAULT_UPDATE_URL}
    if not CONFIG.exists():
        return cfg
    try:
        loaded = json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return cfg
    if loaded.get("url"):
        cfg["url"] = loaded["url"].strip()
    return cfg


def save_url(url: str) -> None:
    CONFIG.parent.mkdir(exist_ok=True)
    cfg = load_config()
    cfg["url"] = url.strip()
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def normalized(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def same_bytes(path: Path, want: str) -> bool:
    """True when the file matches, including Windows CRLF vs LF."""
    if not path.is_file() or not want:
        return False
    raw = path.read_bytes()
    want = want.lower()
    return sha256_bytes(raw) == want or sha256_bytes(normalized(raw)) == want


def applied_version() -> str:
    if not APPLIED.exists():
        return ""
    return normalized(APPLIED.read_bytes()).decode("utf-8", "replace").strip()


def mark_applied(version: str) -> None:
    if not version:
        return
    APPLIED.parent.mkdir(exist_ok=True)
    APPLIED.write_bytes((version.strip() + "\n").encode("utf-8"))


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
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MusicStream-Updater",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    return urllib.request.urlopen(request, timeout=timeout)


def fetch_json(url: str) -> dict:
    fresh = url + ("&" if "?" in url else "?") + "t=" + str(int(time.time()))
    with _get(fresh, 30) as response:
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
        if same_bytes(dest, want):
            continue
        file_url = item.get("url")
        if not file_url:
            continue
        log(f"downloading {rel}")
        download(file_url, dest)
        if want and not same_bytes(dest, want):
            raise RuntimeError(f"hash mismatch for {rel}")
        changed.append(rel)
    if remote:
        payload = (remote.strip() + "\n").encode("utf-8")
        current = VERSION_FILE.read_bytes() if VERSION_FILE.exists() else b""
        if normalized(current) != payload:
            VERSION_FILE.write_bytes(payload)
    return {"ok": True, "local": local, "remote": remote, "changed": changed}
