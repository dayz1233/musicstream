# MusicStream Windows - Desktop App Edition

## Super Simple Startup

1. Double-click `start.bat`
2. A desktop window opens (no browser required for the backend).
3. Drag and drop your Spotify playlist CSV files onto the drop zone.
4. Click **Start Server** inside the window when you want the full streaming/API backend.

## What the desktop window does

- Drag-and-drop (or click to browse) Spotify export CSVs
- Parses Track Name, Artist, Duration, Genres, etc.
- Saves them as JSON for recommendations and media-server matching
- Shows a live log of all processes (parsing, server output, validation status)
- Start/Stop server buttons
- Open Web UI button (optional — the desktop window is the main control surface)
- Storage sorting trigger

## Spotify CSV format

Your file (e.g. country.csv) is already in the expected format:
- Track Name
- Artist Name(s)
- Duration (ms)
- Genres
- etc.

Just drop the file(s) on the window.

## Hands-off features

- Background hourly hot/cold storage sorting still runs once the server is started
- Recommendation filtering by listened tracks
- Cross-device controls
- Automation sequences for your non-API media server

Default password (if you open the web UI): admin / changeme

## Updates without reinstalling

Paste an update URL once in the desktop window and click Save. On each launch the program checks that address and downloads only files whose contents changed. Your music folders, playlists, and database are never replaced, and Python is not reinstalled.

The address should point at a small JSON file:

```json
{
  "version": "1.1.0",
  "files": [
    {"path": "main.py", "sha256": "<sha256 of the file>", "url": "https://example.com/main.py"}
  ]
}
```

A GitHub repository raw file works. After this copy is installed, later program changes can be published there and this window picks them up.

