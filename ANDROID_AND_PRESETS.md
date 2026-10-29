# MusicStream – Android + Desktop Edition

## What's included

### 1. Desktop / Server (Python FastAPI)
- Original music library + streaming
- **New: Pre-set Actions** section — save named queries that hit your media server
- **New: Query Your Media Server** — search bar that returns results for verification
- **New: Playlist Verification** placeholder (`/api/verify-playlist`) — ready for the Spotify recommendation playlist file you will provide
- Phone can search via `/api/search-from-phone`; results appear in the desktop UI

### 2. Android App Skeleton (Kotlin + Media3)
- Proper background playback using `MediaSessionService` + ExoPlayer
- Continues when screen is off / phone is asleep
- Media controls in notification and lock screen
- Bluetooth audio routing and media button support (handled by Media3)
- Multiple outputs: phone speaker or Bluetooth speaker (system handles routing; app requests media audio focus)

**Important:** This is source code. Open the `musicstream-android` folder in Android Studio, let it sync Gradle, and build/install the APK.

## How the pieces work together

1. Run the Python server on your Windows PC (`python main.py`).
2. Point the Android app at `http://YOUR_PC_IP:8000`.
3. On the desktop UI, use **Presets & Server** to program queries against your existing media server.
4. When you search from the phone, the desktop can display the results for you to verify against your Spotify playlist info file.

## Next steps you can request
- Wire the `/api/query-server` endpoint to your real media server API (tell me the endpoint format).
- Parse and verify against the Spotify playlist file once you upload it.
- Add a simple queue / next-track support in the Android app.
- Add cover art and better recommendations.

See the original README.md for basic server setup.
