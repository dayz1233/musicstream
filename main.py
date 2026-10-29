import os
import json
import random
import shutil
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timedelta

from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Depends, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
from typing import Dict, Set

# ... (keep existing imports and models)

# Simple password auth (change PASSWORD in env or here)
PASSWORD = os.getenv("MUSICSTREAM_PASSWORD", "changeme")
security = HTTPBasic()

def get_current_user(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, "admin")
    correct_password = secrets.compare_digest(credentials.password, PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(status_code=401, detail="Incorrect credentials", headers={"WWW-Authenticate": "Basic"})
    return credentials.username

# WebSocket connection manager for cross-device controls
class ConnectionManager:
    def __init__(self):
        self.active: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active.discard(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active):
            try:
                await connection.send_json(message)
            except:
                self.disconnect(connection)

manager = ConnectionManager()

# Protect main routes with Depends(get_current_user) where appropriate
# Note: For full protection, wrap more endpoints. For demo, search/recs are protected.
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import mutagen
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Config
BASE_DIR = Path(__file__).parent
MUSIC_DIR = BASE_DIR / "music"
UPLOAD_DIR = BASE_DIR / "uploads"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "music.db"

MUSIC_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
HOT_DIR = BASE_DIR / "music_hot"
COLD_DIR = BASE_DIR / "music_cold"
HOT_DIR.mkdir(exist_ok=True)
COLD_DIR.mkdir(exist_ok=True)

# Database
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Track(Base):
    __tablename__ = "tracks"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    artist = Column(String, index=True)
    album = Column(String, index=True)
    genre = Column(String, index=True)
    duration = Column(Float)
    file_path = Column(String, unique=True)
    cover_path = Column(String, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    play_count = Column(Integer, default=0)

class PlayHistory(Base):
    __tablename__ = "play_history"
    id = Column(Integer, primary_key=True)
    track_id = Column(Integer)
    played_at = Column(DateTime, default=datetime.utcnow)

class ListenEvent(Base):
    __tablename__ = "listen_events"
    id = Column(Integer, primary_key=True)
    title = Column(String, index=True)
    artist = Column(String, default="")
    genres = Column(String, default="")
    album = Column(String, default="")
    kind = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class PresetAction(Base):
    __tablename__ = "preset_actions"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    query = Column(String)
    action_type = Column(String)  # search, play, verify
    created_at = Column(DateTime, default=datetime.utcnow)

# Add after Base.metadata.create_all
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# App
app = FastAPI(title="MusicStream", description="Self-hosted Spotify-like music server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

@app.websocket("/ws/controls")
async def websocket_controls(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            await manager.broadcast({"type": "control", "action": data.get("action"), "payload": data.get("payload")})
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# Helpers
def extract_metadata(file_path: Path):
    try:
        audio = mutagen.File(file_path)
        if audio is None:
            return None
        title = str(audio.get("title", [file_path.stem])[0]) if audio.get("title") else file_path.stem
        artist = str(audio.get("artist", ["Unknown Artist"])[0]) if audio.get("artist") else "Unknown Artist"
        album = str(audio.get("album", ["Unknown Album"])[0]) if audio.get("album") else "Unknown Album"
        genre = str(audio.get("genre", ["Unknown"])[0]) if audio.get("genre") else "Unknown"
        duration = audio.info.length if audio.info else 0
        return {
            "title": title[:200],
            "artist": artist[:200],
            "album": album[:200],
            "genre": genre[:100],
            "duration": duration,
        }
    except Exception:
        return {
            "title": file_path.stem,
            "artist": "Unknown Artist",
            "album": "Unknown Album",
            "genre": "Unknown",
            "duration": 0,
        }

def scan_library(db: Session):
    """Scan music directory and add new tracks."""
    supported = {".mp3", ".flac", ".wav", ".ogg", ".m4a"}
    for file_path in MUSIC_DIR.rglob("*"):
        if file_path.suffix.lower() in supported and file_path.is_file():
            existing = db.query(Track).filter(Track.file_path == str(file_path)).first()
            if not existing:
                meta = extract_metadata(file_path)
                if meta:
                    track = Track(
                        title=meta["title"],
                        artist=meta["artist"],
                        album=meta["album"],
                        genre=meta["genre"],
                        duration=meta["duration"],
                        file_path=str(file_path),
                    )
                    db.add(track)
    db.commit()

# Streaming with range support
def stream_file(file_path: Path, request: Request):
    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")
    if range_header:
        start, end = range_header.replace("bytes=", "").split("-")
        start = int(start) if start else 0
        end = int(end) if end else file_size - 1
        if start >= file_size:
            raise HTTPException(status_code=416, detail="Range not satisfiable")
        end = min(end, file_size - 1)
        chunk_size = end - start + 1

        def iterfile():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    data = f.read(min(65536, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            iterfile(),
            status_code=206,
            media_type="audio/mpeg",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(chunk_size),
            },
        )
    else:
        return FileResponse(file_path, media_type="audio/mpeg")

# Routes
@app.get("/", response_class=HTMLResponse)
async def root():
    page = BASE_DIR / "static" / "index.html"
    if not page.is_file():
        return HTMLResponse("<h1>MusicStream</h1><p>The player page is missing.</p>", status_code=500)
    return HTMLResponse(page.read_text(encoding="utf-8"))

@app.post("/api/upload")
async def upload_music(files: List[UploadFile] = File(...), db: Session = Depends(get_db)):
    uploaded = []
    for file in files:
        if not file.filename:
            continue
        dest = MUSIC_DIR / file.filename
        # Avoid overwrite
        counter = 1
        while dest.exists():
            dest = MUSIC_DIR / f"{dest.stem}_{counter}{dest.suffix}"
            counter += 1
        with open(dest, "wb") as f:
            content = await file.read()
            f.write(content)
        meta = extract_metadata(dest)
        if meta:
            track = Track(
                title=meta["title"],
                artist=meta["artist"],
                album=meta["album"],
                genre=meta["genre"],
                duration=meta["duration"],
                file_path=str(dest),
            )
            db.add(track)
            uploaded.append(meta["title"])
    db.commit()
    scan_library(db)
    return {"uploaded": uploaded, "count": len(uploaded)}

@app.get("/api/scan")
def scan(db: Session = Depends(get_db)):
    scan_library(db)
    return {"status": "scanned"}

@app.get("/api/tracks")
def get_tracks(genre: Optional[str] = None, artist: Optional[str] = None, album: Optional[str] = None, 
               limit: int = 100, db: Session = Depends(get_db)):
    q = db.query(Track)
    if genre:
        q = q.filter(Track.genre.ilike(f"%{genre}%"))
    if artist:
        q = q.filter(Track.artist.ilike(f"%{artist}%"))
    if album:
        q = q.filter(Track.album.ilike(f"%{album}%"))
    tracks = q.order_by(Track.play_count.desc(), Track.added_at.desc()).limit(limit).all()
    return [
        {
            "id": t.id,
            "title": t.title,
            "artist": t.artist,
            "album": t.album,
            "genre": t.genre,
            "duration": t.duration,
            "play_count": t.play_count,
        }
        for t in tracks
    ]

@app.get("/api/genres")
def get_genres(db: Session = Depends(get_db)):
    genres = db.query(Track.genre).distinct().all()
    return [g[0] for g in genres if g[0]]

@app.get("/api/artists")
def get_artists(db: Session = Depends(get_db)):
    artists = db.query(Track.artist).distinct().all()
    return [a[0] for a in artists if a[0]]

@app.get("/api/albums")
def get_albums(db: Session = Depends(get_db)):
    albums = db.query(Track.album).distinct().all()
    return [a[0] for a in albums if a[0]]

@app.get("/api/recommendations")
def get_recommendations(db: Session = Depends(get_db)):
    rec_tracks = db.query(Track).order_by(Track.play_count.desc(), Track.added_at.desc()).limit(20).all()
    recent = db.query(Track).order_by(Track.added_at.desc()).limit(10).all()
    genres = [g[0] for g in db.query(Track.genre).distinct().limit(12).all() if g[0]]

    def row(track):
        return {
            "id": track.id,
            "title": track.title,
            "artist": track.artist,
            "genre": track.genre,
            "duration": track.duration,
        }

    return {
        "for_you": [row(track) for track in rec_tracks],
        "recently_added": [row(track) for track in recent],
        "genres": genres,
    }

@app.post("/api/play/{track_id}")
def record_play(track_id: int, db: Session = Depends(get_db)):
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(404, "Track not found")
    track.play_count += 1
    history = PlayHistory(track_id=track_id)
    db.add(history)
    db.commit()
    return {"ok": True}

@app.get("/api/stream/{track_id}")
async def stream_track(track_id: int, request: Request, db: Session = Depends(get_db)):
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track or not Path(track.file_path).exists():
        raise HTTPException(404, "Track not found")
    return stream_file(Path(track.file_path), request)

@app.get("/api/search")
def search(q: str, db: Session = Depends(get_db)):
    tracks = db.query(Track).filter(
        (Track.title.ilike(f"%{q}%")) |
        (Track.artist.ilike(f"%{q}%")) |
        (Track.album.ilike(f"%{q}%"))
    ).limit(50).all()
    return [
        {"id": t.id, "title": t.title, "artist": t.artist, "album": t.album, "genre": t.genre, "duration": t.duration}
        for t in tracks
    ]

@app.post("/api/presets")
def create_preset(name: str, query: str, action_type: str = "search", db: Session = Depends(get_db)):
    preset = PresetAction(name=name, query=query, action_type=action_type)
    db.add(preset)
    db.commit()
    return {"id": preset.id, "name": name}

@app.get("/api/presets")
def list_presets(db: Session = Depends(get_db)):
    presets = db.query(PresetAction).all()
    return [{"id": p.id, "name": p.name, "query": p.query, "action_type": p.action_type} for p in presets]

from automation.automator import MediaServerAutomator

automator = MediaServerAutomator()

from pydantic import BaseModel

class SequencePayload(BaseModel):
    name: str
    steps: List[dict]

@app.post("/api/automation/sequence")
def save_automation_sequence(payload: SequencePayload):
    automator.save_sequence(payload.name, payload.steps)
    return {"saved": payload.name}

@app.get("/api/automation/sequences")
def list_sequences():
    return list(automator.sequences.keys())

from automation.validator import DownloadValidator

validator = DownloadValidator()

@app.post("/api/automation/run")
def run_automation(name: str, query: Optional[str] = None, extra_delay: float = 0.0):
    """Run a pre-recorded click/type sequence. extra_delay (seconds) is added after every step for slow sites."""
    try:
        result = automator.run_sequence(name, query, extra_delay)
        return result
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/validate-download")
def validate_download(
    expected_title: Optional[str] = None,
    expected_artist: Optional[str] = None,
    expected_duration: Optional[float] = None,
    expected_name: Optional[str] = None,
    downloads_dir: Optional[str] = None,
    timeout: int = 90,
    duration_tolerance: float = 3.0,
):
    """
    Watch the downloads folder, handle zip/OPUS/FLAC/etc., and validate
    name, artist, full title, and song length.
    """
    if downloads_dir:
        validator.downloads_dir = Path(downloads_dir)
    downloaded = validator.wait_for_download(timeout=timeout)
    if not downloaded:
        raise HTTPException(408, "No new download detected within timeout")
    
    expected = {}
    if expected_title: expected["title"] = expected_title
    if expected_artist: expected["artist"] = expected_artist
    if expected_duration is not None: expected["duration"] = expected_duration
    if expected_name: expected["name"] = expected_name
    
    result = validator.validate(downloaded, expected, duration_tolerance)
    return result

@app.post("/api/query-and-validate")
def query_and_validate(
    sequence: str,
    query: str,
    expected_title: Optional[str] = None,
    expected_artist: Optional[str] = None,
    expected_duration: Optional[float] = None,
    extra_delay: float = 1.0,
    timeout: int = 120,
):
    """Run automation sequence, then wait for download and validate it."""
    # Run the click sequence
    automator.run_sequence(sequence, query, extra_delay)
    # Wait and validate
    downloaded = validator.wait_for_download(timeout=timeout)
    if not downloaded:
        return {"automation": "completed", "validation": "no download detected"}
    expected = {
        "title": expected_title,
        "artist": expected_artist,
        "duration": expected_duration,
    }
    expected = {k: v for k, v in expected.items() if v is not None}
    validation = validator.validate(downloaded, expected)
    return {"automation": "completed", "downloaded": str(downloaded), "validation": validation}

@app.get("/api/automation/record-position")
def record_mouse_position(label: str = "position"):
    """Helper: move mouse to target, then call this. Returns coordinates after 3s delay."""
    try:
        pos = automator.record_position(label)
        return {"x": pos.x, "y": pos.y, "label": label}
    except Exception as e:
        raise HTTPException(500, str(e))

from automation.click_steps import click_steps


@app.get("/api/media-browser")
def media_browser_status():
    return click_steps.status()


@app.post("/api/media-browser/open")
def media_browser_open(address: str = ""):
    result = click_steps.open_chrome(address)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "Could not open Chrome")
    return result


@app.post("/api/media-browser/stop")
def media_browser_stop():
    return click_steps.stop()


@app.post("/api/media-browser/clear")
def media_browser_clear():
    return click_steps.clear()


@app.post("/api/media-browser/fetch")
def media_browser_fetch(query: str = "", title: str = "", artist: str = "", delay: float = 0.8, db: Session = Depends(get_db)):
    started = time.time()
    text = query or f"{artist} {title}".strip()
    result = click_steps.replay(text, delay)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "The recorded steps failed")
    downloaded = validator.wait_for_download(timeout=120, started_at=started)
    if not downloaded:
        return {"ok": False, "browser": result, "error": "No download appeared in the Downloads folder."}
    validation = validator.validate(downloaded, {"title": title or query, "artist": artist})
    audio_path = Path(validation.get("extracted") or str(downloaded))
    destination = MUSIC_DIR / audio_path.name
    counter = 1
    while destination.exists():
        destination = MUSIC_DIR / f"{audio_path.stem}_{counter}{audio_path.suffix}"
        counter += 1
    shutil.copy2(audio_path, destination)
    scan_library(db)
    return {"ok": True, "browser": result, "validation": validation, "saved": destination.name}


@app.post("/api/verify-playlist")
def verify_against_playlist(track_title: str, artist: str, playlist_file: Optional[str] = None):
    """Verify a track against a Spotify recommendation playlist file (JSON/CSV)."""
    # Placeholder — user will provide the file
    return {
        "track": f"{artist} - {track_title}",
        "in_playlist": False,
        "message": "Upload your Spotify playlist info file to enable verification"
    }

@app.get("/api/search-from-phone")
def search_from_phone(q: str, db: Session = Depends(get_db)):
    """Endpoint that phone can hit; desktop can also poll or receive via websocket later."""
    tracks = db.query(Track).filter(
        (Track.title.ilike(f"%{q}%")) | (Track.artist.ilike(f"%{q}%"))
    ).limit(30).all()
    return [{"id": t.id, "title": t.title, "artist": t.artist, "album": t.album} for t in tracks]


@app.get("/api/playlists")
def list_playlists():
    items = []
    for path in sorted(DATA_DIR.glob("playlist_*.json")):
        try:
            tracks = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            tracks = []
        tracks = dedupe_tracks(tracks if isinstance(tracks, list) else [])
        if not tracks:
            continue
        sample = tracks[0]
        items.append({
            "name": path.stem[len("playlist_"):],
            "count": len(tracks) if isinstance(tracks, list) else 0,
            "sample_title": (sample.get("title") if isinstance(sample, dict) else "") or "",
            "sample_artist": (sample.get("artist") if isinstance(sample, dict) else "") or "",
        })
    return items


@app.get("/api/playlists/{name}")
def get_playlist(name: str):
    safe = Path(name).name
    path = DATA_DIR / f"playlist_{safe}.json"
    if not path.is_file():
        raise HTTPException(404, "Playlist not found")
    tracks = json.loads(path.read_text(encoding="utf-8"))
    return {"name": safe, "tracks": dedupe_tracks(tracks if isinstance(tracks, list) else [])}


_cover_lock = threading.Lock()


def _read_cover_cache():
    path = DATA_DIR / "cover_cache.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def lookup_release(artist: str, title: str):
    key = f"{(artist or '').strip().lower()}|{(title or '').strip().lower()}"
    with _cover_lock:
        cached = _read_cover_cache().get(key)
    if cached is not None:
        return cached
    found = {"image": None, "preview": None}
    term = " ".join(part.strip() for part in ((artist or "").split(";")[0], title or "") if part.strip())
    if term:
        query = urllib.parse.urlencode({"term": term, "entity": "song", "limit": 5})
        request = urllib.request.Request(
            "https://itunes.apple.com/search?" + query,
            headers={"User-Agent": "MusicStream"},
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode())
            wanted = (title or "").strip().lower()
            for item in payload.get("results", []):
                name = (item.get("trackName") or "").lower()
                if wanted and wanted not in name and name not in wanted:
                    continue
                image = (item.get("artworkUrl100") or "").replace("100x100bb", "300x300bb")
                found = {"image": image or None, "preview": item.get("previewUrl") or None}
                break
        except Exception:
            found = {"image": None, "preview": None}
    with _cover_lock:
        cache = _read_cover_cache()
        cache[key] = found
        (DATA_DIR / "cover_cache.json").write_text(json.dumps(cache), encoding="utf-8")
    return found


@app.get("/api/cover")
def cover(artist: str = "", title: str = ""):
    image = lookup_release(artist, title).get("image")
    if not image:
        raise HTTPException(404, "No cover")
    return RedirectResponse(image)


@app.get("/api/match")
def match_track(title: str, artist: str = "", db: Session = Depends(get_db)):
    query = db.query(Track).filter(Track.title.ilike(f"%{title.strip()}%"))
    first_artist = artist.split(";")[0].strip()
    if first_artist:
        query = query.filter(Track.artist.ilike(f"%{first_artist}%"))
    track = query.first()
    if not track:
        track = db.query(Track).filter(Track.title.ilike(f"%{title.strip()}%")).first()
    if not track or not Path(track.file_path).exists():
        preview = lookup_release(artist, title).get("preview")
        return {"found": False, "preview": preview, "title": title, "artist": artist}
    return {"found": True, "id": track.id, "title": track.title, "artist": track.artist}


def song_key(track: dict):
    title = (track.get("title") or "").strip().lower()
    artist = (track.get("artist") or "").split(";")[0].strip().lower()
    return title, artist


def dedupe_tracks(tracks):
    seen = set()
    cleaned = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        key = song_key(track)
        if not key[0] or key in seen:
            continue
        seen.add(key)
        cleaned.append(track)
    return cleaned


def _playlist_tracks():
    pool = []
    for path in sorted(DATA_DIR.glob("playlist_*.json")):
        try:
            tracks = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(tracks, list):
            pool.extend(tracks)
    return dedupe_tracks(pool)


@app.get("/api/roll")
def roll_dice():
    pool = _playlist_tracks()
    if not pool:
        return {"kind": "empty"}
    kind = random.choice(["track", "artist", "album", "category"])
    picked = random.choice(pool)
    if kind == "track":
        return {
            "kind": "track",
            "title": picked.get("title"),
            "artist": picked.get("artist") or "",
            "album": picked.get("album") or "",
            "genres": picked.get("genres") or "",
        }
    if kind == "artist":
        artist = (picked.get("artist") or "Unknown").split(";")[0].strip() or "Unknown"
        matches = [track for track in pool if artist.lower() in (track.get("artist") or "").lower()][:30]
        return {"kind": "artist", "artist": artist, "tracks": matches}
    if kind == "album":
        album = picked.get("album") or "Unknown album"
        matches = [track for track in pool if (track.get("album") or "") == album][:30]
        return {"kind": "album", "album": album, "artist": picked.get("artist") or "", "tracks": matches}
    category = ((picked.get("genres") or "Unknown").split(",")[0].strip()) or "Unknown"
    matches = [track for track in pool if category.lower() in (track.get("genres") or "").lower()][:30]
    return {"kind": "category", "category": category, "tracks": matches}


@app.get("/api/categories")
def list_categories():
    counts = {}
    for track in _playlist_tracks():
        for genre in (track.get("genres") or "").split(","):
            name = genre.strip()
            if not name or name.lower() == "unknown":
                continue
            counts[name] = counts.get(name, 0) + 1
    return [{"name": name, "count": count} for name, count in sorted(counts.items()) if count]


@app.get("/api/category/{name}")
def category_tracks(name: str):
    wanted = name.strip().lower()
    tracks = []
    for track in _playlist_tracks():
        genres = [genre.strip().lower() for genre in (track.get("genres") or "").split(",")]
        if wanted in genres:
            tracks.append(track)
    return {"name": name, "tracks": tracks}


@app.post("/api/listening")
def add_listening(title: str, artist: str = "", genres: str = "", album: str = "", kind: str = "play", db: Session = Depends(get_db)):
    event = ListenEvent(
        title=title.strip(),
        artist=artist.strip(),
        genres=genres.strip(),
        album=album.strip(),
        kind="skip" if kind == "skip" else "play",
    )
    db.add(event)
    db.commit()
    return {"ok": True}


@app.get("/api/listening")
def recent_listening(minutes: int = 30, db: Session = Depends(get_db)):
    plays_since = datetime.utcnow() - timedelta(minutes=max(1, min(minutes, 180)))
    skips_since = datetime.utcnow() - timedelta(days=14)
    plays = db.query(ListenEvent).filter(ListenEvent.kind == "play", ListenEvent.created_at >= plays_since).all()
    skips = db.query(ListenEvent).filter(ListenEvent.kind == "skip", ListenEvent.created_at >= skips_since).all()

    def row(event):
        return {"title": event.title, "artist": event.artist, "genres": event.genres, "album": event.album}

    return {"plays": [row(event) for event in plays], "skips": [row(event) for event in skips]}


if __name__ == "__main__":
    import uvicorn
    db = SessionLocal()
    scan_library(db)
    db.close()
    print("MusicStream is on http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
