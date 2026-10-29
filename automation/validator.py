"""
Post-download validation for media server files.
Handles zip archives, unusual formats (OPUS, FLAC, etc.), and validates
name, artist, full title, and duration against expected values.
"""

import os
import zipfile
import tempfile
import shutil
from pathlib import Path
from typing import Optional, Dict, Any
import time

try:
    import mutagen
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

class DownloadValidator:
    def __init__(self, downloads_dir: str = None):
        self.downloads_dir = Path(downloads_dir or Path.home() / "Downloads")
        self.temp_dir = Path(tempfile.gettempdir()) / "musicstream_extract"
        self.temp_dir.mkdir(exist_ok=True)

    def wait_for_download(self, timeout: int = 120, poll_interval: float = 1.0) -> Optional[Path]:
        """Watch downloads folder for a new file. Returns path when size stabilizes."""
        start = time.time()
        before = set(self.downloads_dir.glob("*"))
        while time.time() - start < timeout:
            after = set(self.downloads_dir.glob("*"))
            new_files = after - before
            if new_files:
                # Wait for the largest new file to finish writing
                candidate = max(new_files, key=lambda p: p.stat().st_size if p.is_file() else 0)
                if candidate.is_file():
                    size1 = candidate.stat().st_size
                    time.sleep(2)
                    size2 = candidate.stat().st_size
                    if size1 == size2 and size1 > 0:
                        return candidate
            time.sleep(poll_interval)
        return None

    def extract_if_zip(self, file_path: Path) -> Path:
        """If zip, extract and return the first audio file found. Otherwise return original."""
        if file_path.suffix.lower() == ".zip":
            extract_to = self.temp_dir / file_path.stem
            extract_to.mkdir(exist_ok=True)
            with zipfile.ZipFile(file_path, "r") as z:
                z.extractall(extract_to)
            # Find first audio file
            for ext in [".mp3", ".flac", ".opus", ".ogg", ".m4a", ".wav", ".aac"]:
                for f in extract_to.rglob(f"*{ext}"):
                    return f
            # Fallback: return the zip itself if no audio found
            return file_path
        return file_path

    def get_audio_info(self, file_path: Path) -> Dict[str, Any]:
        """Extract title, artist, album, duration from any supported format."""
        if not HAS_MUTAGEN:
            return {"error": "mutagen not installed", "duration": 0}
        try:
            audio = mutagen.File(file_path)
            if audio is None:
                return {"title": file_path.stem, "artist": "", "duration": 0, "format": file_path.suffix}
            
            # Common tags
            title = self._get_tag(audio, ["title", "TIT2", "©nam"]) or file_path.stem
            artist = self._get_tag(audio, ["artist", "TPE1", "©ART"]) or ""
            album = self._get_tag(audio, ["album", "TALB", "©alb"]) or ""
            duration = getattr(audio.info, "length", 0) or 0
            
            return {
                "title": str(title),
                "artist": str(artist),
                "album": str(album),
                "duration": round(duration, 2),
                "format": file_path.suffix.lower(),
                "file": str(file_path),
            }
        except Exception as e:
            return {"error": str(e), "title": file_path.stem, "duration": 0, "format": file_path.suffix}

    def _get_tag(self, audio, keys):
        for k in keys:
            if k in audio:
                val = audio[k]
                if isinstance(val, list):
                    return val[0]
                return val
        return None

    def validate(self, file_path: Path, expected: Dict[str, Any], duration_tolerance: float = 3.0) -> Dict:
        """
        Validate downloaded file against expected name/artist/title/length.
        expected = {"title": "...", "artist": "...", "duration": 180.5}
        """
        audio_file = self.extract_if_zip(file_path)
        info = self.get_audio_info(audio_file)
        
        result = {
            "file": str(file_path),
            "extracted": str(audio_file) if audio_file != file_path else None,
            "info": info,
            "matches": {},
            "passed": True,
            "issues": [],
        }
        
        # Title / full title check (case-insensitive partial or exact)
        if "title" in expected and expected["title"]:
            got = (info.get("title") or "").lower()
            exp = expected["title"].lower()
            match = exp in got or got in exp
            result["matches"]["title"] = match
            if not match:
                result["passed"] = False
                result["issues"].append(f"Title mismatch: got '{info.get('title')}' expected '{expected['title']}'")
        
        # Artist check
        if "artist" in expected and expected["artist"]:
            got = (info.get("artist") or "").lower()
            exp = expected["artist"].lower()
            match = exp in got or got in exp
            result["matches"]["artist"] = match
            if not match:
                result["passed"] = False
                result["issues"].append(f"Artist mismatch: got '{info.get('artist')}' expected '{expected['artist']}'")
        
        # Duration check
        if "duration" in expected and expected["duration"]:
            got = info.get("duration", 0)
            exp = float(expected["duration"])
            diff = abs(got - exp)
            match = diff <= duration_tolerance
            result["matches"]["duration"] = match
            result["duration_diff"] = round(diff, 2)
            if not match:
                result["passed"] = False
                result["issues"].append(f"Duration mismatch: got {got}s expected {exp}s (diff {diff:.1f}s)")
        
        # Name (filename) check
        if "name" in expected and expected["name"]:
            got = file_path.stem.lower()
            exp = expected["name"].lower()
            match = exp in got or got in exp
            result["matches"]["name"] = match
            if not match:
                result["issues"].append(f"Filename mismatch: '{file_path.name}'")
        
        return result

    def cleanup(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
