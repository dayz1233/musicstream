# MusicStream

A self-hosted Spotify-like music server. Upload your music, stream it over your local network to any device (including Android phones) via the browser — no app install required. Includes categories, recommendations based on play history, and a modern dark UI.

## Features

- Upload music via the web interface (MP3, FLAC, WAV, OGG, M4A)
- Automatic metadata extraction (title, artist, album, genre)
- Categories: Genres, Artists, Albums, Library
- Recommendations ("Made for you" based on play counts + recently added)
- Full audio streaming with seeking (range requests)
- Responsive web player that works great on phones
- Local network access — open on your computer and connect from your phone

## Requirements

- Python 3.9+
- Your computer on the same Wi-Fi / network as your phone

## Setup

1. Unzip this folder.

2. Open a terminal in the `musicstream` folder.

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Run the server:

```bash
python main.py
```

The server starts on `http://0.0.0.0:8000`.

## Access from your phone

1. On the computer running the server, find its local IP address:
   - Windows: `ipconfig` → look for IPv4 Address (e.g. 192.168.1.42)
   - Mac/Linux: `ifconfig` or `ip addr` → look for inet under your Wi-Fi interface

2. On your Android phone (same Wi-Fi), open Chrome and go to:
   ```
   http://YOUR_COMPUTER_IP:8000
   ```
   Example: `http://192.168.1.42:8000`

3. Upload music using the Upload section, or drop files into the `music/` folder and click Scan (or restart the server).

## Tips

- Place existing music in the `music/` folder before starting, then restart or call `/api/scan`.
- The database and play history are stored in `data/music.db`.
- For better recommendations, play some tracks — the "Made for you" section improves with usage.
- Works fully offline on your local network. No cloud, no accounts.

## Security Note

This is designed for your private local network. Do not expose port 8000 to the internet without adding authentication.

Enjoy your personal Spotify!
