"""
MusicStream Desktop Launcher
Simple application window with drag-and-drop for Spotify playlist CSVs.
Launches the backend server in the background and shows process status.
"""
import os
import sys
import csv
import json
import threading
import time
import subprocess
import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog, simpledialog
from pathlib import Path
import updater
import webbrowser
import socket
import urllib.request
import urllib.parse
import urllib.error

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False
    print("tkinterdnd2 not available — using file dialog fallback")

BASE_DIR = Path(__file__).parent
SERVER_PROCESS = None


def lan_address():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.4.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()

class MusicStreamApp:
    def __init__(self, root):
        self.root = root
        self.root.title("MusicStream Desktop")
        self.root.geometry("980x860")
        self.root.configure(bg="#1a1a1a")

        # Header
        header = tk.Label(root, text="MusicStream", font=("Segoe UI", 20, "bold"),
                          fg="#1db954", bg="#1a1a1a")
        header.pack(pady=10)

        sub = tk.Label(root, text="Drag a folder of Spotify CSVs, or individual files",
                       fg="#b3b3b3", bg="#1a1a1a", font=("Segoe UI", 10))
        sub.pack()

        # Drop zone
        self.drop_frame = tk.Frame(root, bg="#2a2a2a", relief="ridge", bd=2)
        self.drop_frame.pack(fill="x", padx=20, pady=10, ipady=40)

        self.drop_label = tk.Label(self.drop_frame,
                                    text="Drop a folder of CSV files here\nor click to choose a folder",
                                    fg="#ffffff", bg="#2a2a2a", font=("Segoe UI", 12))
        self.drop_label.pack(fill="both", expand=True)
        self.drop_label.bind("<Button-1>", lambda e: self.browse_csv())

        if HAS_DND:
            for widget in (self.root, self.drop_frame, self.drop_label):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self.on_drop)
        self.drop_frame.bind("<Button-1>", lambda e: self.browse_csv())

        # Buttons
        btn_frame = tk.Frame(root, bg="#1a1a1a")
        btn_frame.pack(pady=5)
        tk.Button(btn_frame, text="Import folder", command=self.browse_csv,
                  bg="#1db954", fg="white", relief="flat", padx=15).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Stop Server", command=self.stop_server,
                  bg="#c45c4a", fg="white", relief="flat", padx=15).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Open Web UI", command=self.open_browser,
                  bg="#333", fg="white", relief="flat", padx=15).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Sort Storage Now", command=self.trigger_sort,
                  bg="#333", fg="white", relief="flat", padx=15).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Check for updates", command=self.check_updates,
                  bg="#333", fg="white", relief="flat", padx=15).pack(side="left", padx=5)

        url_row = tk.Frame(root, bg="#1a1a1a")
        url_row.pack(fill="x", padx=20, pady=4)
        tk.Label(url_row, text="Update URL", fg="#b3b3b3", bg="#1a1a1a").pack(side="left")
        self.update_url = tk.Entry(url_row, bg="#2a2a2a", fg="#ffffff", insertbackground="white")
        self.update_url.pack(side="left", fill="x", expand=True, padx=8)
        self.update_url.insert(0, updater.load_config().get("url") or updater.DEFAULT_UPDATE_URL)
        tk.Button(url_row, text="Save", command=self.save_update_url,
                  bg="#333", fg="white", relief="flat").pack(side="left")

        tk.Label(root, text="Media server browser", fg="#ffffff", bg="#1a1a1a",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=20, pady=(8, 0))
        media_row = tk.Frame(root, bg="#1a1a1a")
        media_row.pack(fill="x", padx=20, pady=4)
        self.media_url = tk.Entry(media_row, bg="#2a2a2a", fg="#ffffff", insertbackground="white")
        self.media_url.pack(side="left", fill="x", expand=True, padx=(0, 8))
        tk.Button(media_row, text="Open media browser", command=self.open_media_browser,
                  bg="#1db954", fg="white", relief="flat", padx=10).pack(side="left", padx=4)
        tk.Button(media_row, text="Stop", command=self.stop_media_recording,
                  bg="#333", fg="white", relief="flat", padx=10).pack(side="left", padx=4)
        tk.Button(media_row, text="Clear", command=self.clear_media_recording,
                  bg="#c45c4a", fg="white", relief="flat", padx=10).pack(side="left", padx=4)
        self.record_notice = tk.Label(root, text="", fg="#c45c4a", bg="#1a1a1a", font=("Segoe UI", 12, "bold"))
        self.record_notice.pack(anchor="w", padx=20, pady=(4, 0))
        tk.Label(root, text="Chrome window map. Drag a point to move that click. Click a point to set its time.",
                 fg="#b3b3b3", bg="#1a1a1a").pack(anchor="w", padx=20, pady=(6, 0))
        self.map_canvas = tk.Canvas(root, width=640, height=400, bg="#10140f", highlightthickness=1, highlightbackground="#333")
        self.map_canvas.pack(padx=20, pady=4)
        self.map_canvas.bind("<Button-1>", self.map_press)
        self.map_canvas.bind("<B1-Motion>", self.map_drag)
        self.map_canvas.bind("<ButtonRelease-1>", self.map_release)
        self.chrome_window = {"x": 40, "y": 40, "width": 1280, "height": 800}
        self.map_drag_index = None
        self.map_drag_moved = False
        self.step_list = tk.Listbox(root, selectmode="extended", height=6, bg="#121212", fg="#e0e0e0",
                                     selectbackground="#1db954", selectforeground="#04210f",
                                     font=("Consolas", 10), exportselection=False, activestyle="none")
        self.step_list.pack(fill="x", padx=20, pady=4)
        self.step_list.bind("<<ListboxSelect>>", self.fill_step_fields)
        self.cached_steps = []
        edit_row = tk.Frame(root, bg="#1a1a1a")
        edit_row.pack(fill="x", padx=20, pady=4)
        tk.Label(edit_row, text="Seconds to next", fg="#b3b3b3", bg="#1a1a1a").pack(side="left")
        self.step_wait = tk.Entry(edit_row, width=6, bg="#2a2a2a", fg="#ffffff", insertbackground="white")
        self.step_wait.pack(side="left", padx=6)
        tk.Label(edit_row, text="Name", fg="#b3b3b3", bg="#1a1a1a").pack(side="left")
        self.step_name = tk.Entry(edit_row, width=16, bg="#2a2a2a", fg="#ffffff", insertbackground="white")
        self.step_name.pack(side="left", padx=6)
        tk.Button(edit_row, text="Apply to selected", command=self.apply_steps,
                  bg="#333", fg="white", relief="flat", padx=8).pack(side="left", padx=4)
        run_row = tk.Frame(root, bg="#1a1a1a")
        run_row.pack(fill="x", padx=20, pady=4)
        tk.Button(run_row, text="E-STOP", command=self.estop_media,
                  bg="#c45c4a", fg="white", relief="flat", padx=16).pack(side="left")
        self.test_query = tk.Entry(run_row, width=18, bg="#2a2a2a", fg="#ffffff", insertbackground="white")
        self.test_query.pack(side="left", padx=8)
        self.test_query.insert(0, "test song")
        tk.Button(run_row, text="Test", command=self.test_media,
                  bg="#1db954", fg="white", relief="flat", padx=12).pack(side="left")
        tk.Label(run_row, text="Download wait", fg="#b3b3b3", bg="#1a1a1a").pack(side="left", padx=(12, 4))
        self.download_wait = tk.Entry(run_row, width=6, bg="#2a2a2a", fg="#ffffff", insertbackground="white")
        self.download_wait.pack(side="left")
        self.download_wait.insert(0, "120")
        tk.Button(run_row, text="Save", command=self.save_download_wait,
                  bg="#333", fg="white", relief="flat", padx=8).pack(side="left", padx=4)

        # Status / process log
        tk.Label(root, text="Processes & Validation", fg="#ffffff", bg="#1a1a1a",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=20)
        self.log = scrolledtext.ScrolledText(root, height=8, bg="#121212", fg="#e0e0e0",
                                              font=("Consolas", 9), insertbackground="white")
        self.log.pack(fill="both", expand=True, padx=20, pady=5)

        self.log_message("Ready. Drop a folder anywhere on this window, or click Import folder.")
        if HAS_DND:
            self.log_message("Drag-and-drop is on.")
        else:
            self.log_message("Drag-and-drop is off. Install tkinterdnd2, or use Import folder.")
        self.log_message(f"Program version {updater.local_version()}. New versions install themselves.")
        self.media_seen = 0
        self.root.after(800, self.start_server)
        self.root.after(2000, self.schedule_updates)
        self.root.after(3000, self.poll_media_steps)

    def post_local(self, path, query=None):
        def work():
            if not self._server_ready():
                self.root.after(0, lambda: self.log_message(
                    "The music server is not running, so that button could not connect. "
                    "Look for a [server] line above. If there is none, close this window and open MusicStream again."
                ))
                return
            url = "http://127.0.0.1:8000" + path
            if query:
                url += "?" + urllib.parse.urlencode(query)
            request = urllib.request.Request(url, data=b"", method="POST")
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    body = response.read().decode("utf-8", "replace")
                self.root.after(0, lambda body=body: self.log_message(body[:600]))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")
                self.root.after(0, lambda detail=detail: self.log_message("Media browser failed: " + detail[:600]))
            except Exception as exc:
                message = str(exc)
                self.root.after(0, lambda message=message: self.log_message("Media browser failed: " + message))
        threading.Thread(target=work, daemon=True).start()

    def _server_ready(self):
        deadline = time.time() + 40
        restarted = False
        while time.time() < deadline:
            if not restarted and SERVER_PROCESS is not None and SERVER_PROCESS.poll() is not None:
                restarted = True
                self.root.after(0, lambda: self.log_message("The server had stopped. Starting it again."))
                self._start_server()
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/api/media-browser", timeout=2) as response:
                    response.read()
                return True
            except urllib.error.HTTPError:
                return True
            except Exception:
                time.sleep(0.5)
        return False

    def refresh_step_list(self, steps, recording, window=None):
        self.record_notice.config(text="RECORDING" if recording else "")
        if window:
            self.chrome_window = window
        if self.map_drag_moved:
            return
        signature = json.dumps(steps, sort_keys=True)
        if signature == getattr(self, "step_signature", None):
            return
        selected = set(self.step_list.curselection())
        self.cached_steps = steps
        self.step_list.delete(0, "end")
        for step in steps:
            self.step_list.insert("end", f"{step.get('step')}. {step.get('action')}    {step.get('wait', 0)}s to next")
        for index in selected:
            if index < self.step_list.size():
                self.step_list.selection_set(index)
        self.step_signature = signature
        self.draw_map()

    def map_scale(self):
        window = self.chrome_window or {}
        width = float(window.get("width") or 1280)
        height = float(window.get("height") or 800)
        return 640 / width, 400 / height

    def to_canvas(self, x, y):
        window = self.chrome_window or {}
        sx, sy = self.map_scale()
        return (float(x) - float(window.get("x") or 0)) * sx, (float(y) - float(window.get("y") or 0)) * sy

    def to_screen(self, cx, cy):
        window = self.chrome_window or {}
        sx, sy = self.map_scale()
        return int(round(float(window.get("x") or 0) + cx / sx)), int(round(float(window.get("y") or 0) + cy / sy))

    def draw_map(self):
        canvas = self.map_canvas
        canvas.delete("all")
        canvas.create_rectangle(1, 1, 639, 399, outline="#2e7d4f")
        canvas.create_text(12, 12, anchor="w", fill="#6f8f78", text="1280 × 800 Chrome window")
        previous = None
        for index, step in enumerate(self.cached_steps):
            if step.get("x") is None or step.get("y") is None:
                continue
            cx, cy = self.to_canvas(step["x"], step["y"])
            cx = max(8, min(632, cx))
            cy = max(8, min(392, cy))
            if previous:
                canvas.create_line(previous[0], previous[1], cx, cy, fill="#3d6b4f")
            color = "#1db954" if index in self.step_list.curselection() else "#d7ffe8"
            canvas.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, fill=color, outline="#04210f")
            canvas.create_text(cx, cy - 14, fill="#d7ffe8", text=str(step.get("step")), font=("Segoe UI", 8))
            previous = (cx, cy)

    def nearest_step(self, cx, cy):
        best = None
        best_distance = 18
        for index, step in enumerate(self.cached_steps):
            if step.get("x") is None or step.get("y") is None:
                continue
            sx, sy = self.to_canvas(step["x"], step["y"])
            distance = abs(sx - cx) + abs(sy - cy)
            if distance < best_distance:
                best = index
                best_distance = distance
        return best

    def map_press(self, event):
        hit = self.nearest_step(event.x, event.y)
        self.map_drag_moved = False
        if hit is None:
            self.map_drag_index = None
            return
        self.map_drag_index = hit
        self.step_list.selection_clear(0, "end")
        self.step_list.selection_set(hit)
        self.step_list.see(hit)
        self.fill_step_fields()
        self.draw_map()

    def map_drag(self, event):
        if self.map_drag_index is None:
            return
        self.map_drag_moved = True
        step = self.cached_steps[self.map_drag_index]
        x, y = self.to_screen(event.x, event.y)
        step["x"] = x
        step["y"] = y
        self.draw_map()

    def map_release(self, event):
        if self.map_drag_index is None or not self.map_drag_moved:
            self.map_drag_moved = False
            return
        step = self.cached_steps[self.map_drag_index]
        x, y = self.to_screen(event.x, event.y)
        step["x"] = x
        step["y"] = y
        self.step_signature = json.dumps(self.cached_steps, sort_keys=True)
        self.map_drag_moved = False
        self.draw_map()
        self.post_local("/api/media-browser/move", {"index": step.get("step"), "x": x, "y": y})

    def fill_step_fields(self, _event=None):
        picked = self.step_list.curselection()
        if len(picked) != 1:
            return
        step = self.cached_steps[picked[0]]
        self.step_wait.delete(0, "end")
        self.step_wait.insert(0, str(step.get("wait", 0.4)))
        self.step_name.delete(0, "end")
        self.step_name.insert(0, step.get("name") or "")

    def apply_steps(self):
        picked = [str(index + 1) for index in self.step_list.curselection()]
        if not picked:
            self.log_message("Select one or more steps first.")
            return
        query = {"indexes": ",".join(picked)}
        if self.step_wait.get().strip():
            query["wait"] = self.step_wait.get().strip()
        if self.step_name.get().strip():
            query["name"] = self.step_name.get().strip()
        self.step_signature = None
        self.log_message(f"Updating steps {query['indexes']}.")
        self.post_local("/api/media-browser/steps", query)

    def estop_media(self):
        self.log_message("E-stop.")
        self.post_local("/api/media-browser/estop")

    def test_media(self):
        self.log_message("Testing the recorded steps.")
        self.post_local("/api/media-browser/test", {"query": self.test_query.get().strip()})

    def save_download_wait(self):
        seconds = self.download_wait.get().strip() or "120"
        self.log_message(f"Download wait set to {seconds} seconds.")
        self.post_local("/api/media-browser/gap", {"action": "download", "seconds": seconds})

    def open_media_browser(self):
        self.media_seen = 0
        self.log_message("Opening a normal Chrome tab. Clicks and typing are counted as steps. Press Stop when finished.")
        self.post_local("/api/media-browser/open", {"address": self.media_url.get().strip()})
        self.root.after(600, self.poll_media_steps)

    def poll_media_steps(self):
        def work():
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/api/media-browser", timeout=3) as response:
                    data = json.loads(response.read().decode("utf-8", "replace"))
            except Exception:
                self.root.after(1500, self.poll_media_steps)
                return
            steps = data.get("steps") or []
            self.root.after(0, lambda data=data: self.refresh_step_list(
                data.get("steps") or [], bool(data.get("recording")), data.get("window")
            ))
            fresh = steps[self.media_seen:]
            self.media_seen = len(steps)
            for step in fresh:
                line = f"Step {step.get('step')}: {step.get('action')}"
                self.root.after(0, lambda line=line: self.log_message(line))
            message = " ".join(part for part in (data.get("message"), data.get("next")) if part)
            if message and message != getattr(self, "media_message", ""):
                self.media_message = message
                self.root.after(0, lambda message=message: self.log_message(message))
            if data.get("recording") or data.get("message"):
                self.root.after(500, self.poll_media_steps)
            else:
                self.root.after(1500, self.poll_media_steps)
        threading.Thread(target=work, daemon=True).start()

    def stop_media_recording(self):
        self.log_message("Recording stopped.")
        self.post_local("/api/media-browser/stop")

    def clear_media_recording(self):
        self.media_seen = 0
        self.log_message("Steps cleared.")
        self.post_local("/api/media-browser/clear")

    def log_message(self, msg):
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.root.update_idletasks()

    def on_drop(self, event):
        self.log_message("Drop received.")
        self.ingest_paths(self._drop_paths(event.data))

    def _drop_paths(self, data):
        try:
            parts = list(self.root.tk.splitlist(data))
        except Exception:
            parts = [str(data)]
        cleaned = []
        for part in parts:
            text = str(part).strip()
            if text.startswith("{") and text.endswith("}"):
                text = text[1:-1]
            text = text.strip('"')
            if text.lower().startswith("file:"):
                from urllib.parse import unquote, urlparse
                text = unquote(urlparse(text).path)
                if len(text) > 2 and text[0] == "/" and text[2] == ":":
                    text = text[1:]
            if text:
                cleaned.append(text)
        return cleaned

    def browse_csv(self):
        folder = filedialog.askdirectory(title="Select a folder of Spotify CSV files")
        if folder:
            self.ingest_paths([folder])

    def ingest_paths(self, paths):
        csvs = []
        for raw in paths:
            path = Path(str(raw).strip().strip("{}"))
            if not path.exists():
                self.log_message(f"Not found: {path}")
                continue
            if path.is_dir():
                found = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() == ".csv")
                self.log_message(f"Folder {path.name}: {len(found)} CSV file(s), including subfolders.")
                csvs.extend(found)
            else:
                csvs.append(path)
        seen = set()
        for csv_path in csvs:
            key = str(csv_path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            self.process_csv(csv_path)
        if seen:
            self.log_message(f"Finished {len(seen)} CSV file(s).")

    def process_csv(self, path):
        path = Path(path)
        if not path.exists() or path.suffix.lower() != ".csv":
            self.log_message(f"Skipped (not a CSV): {path.name}")
            return
        self.log_message(f"\n=== Processing: {path.name} ===")
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                tracks = list(reader)
            self.log_message(f"Found {len(tracks)} tracks in playlist.")
            # Save parsed data for the server
            out = BASE_DIR / "data" / f"playlist_{path.stem}.json"
            out.parent.mkdir(exist_ok=True)
            simplified = []
            for t in tracks:
                simplified.append({
                    "title": t.get("Track Name", "").strip('"'),
                    "artist": t.get("Artist Name(s)", "").strip('"'),
                    "album": t.get("Album Name", "").strip('"'),
                    "duration_ms": t.get("Duration (ms)", ""),
                    "genres": t.get("Genres", ""),
                    "uri": t.get("Track URI", ""),
                    "popularity": t.get("Popularity", ""),
                    "image": (
                        t.get("Album Image URL")
                        or t.get("Album Cover URL")
                        or t.get("Image")
                        or ""
                    ).strip(),
                })
            with open(out, "w", encoding="utf-8") as f:
                json.dump(simplified, f, indent=2)
            self.log_message(f"Saved parsed playlist to {out.name}")
            self.log_message("Sample tracks:")
            for item in simplified[:5]:
                self.log_message(f"  • {item['title']} — {item['artist']}")
            if len(simplified) > 5:
                self.log_message(f"  ... and {len(simplified)-5} more")
            self.log_message("These will be used for recommendations and media-server matching.")
            # TODO: call server endpoint to import once server is running
        except Exception as e:
            self.log_message(f"Error parsing CSV: {e}")

    def start_server(self):
        global SERVER_PROCESS
        if SERVER_PROCESS and SERVER_PROCESS.poll() is None:
            self.log_message("Server is already running.")
            return
        threading.Thread(target=self._start_server, daemon=True).start()

    def _start_server(self):
        global SERVER_PROCESS
        self.root.after(0, lambda: self.log_message("Installing missing Python packages..."))
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "tkinterdnd2"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "pip failed").strip()[-800:]
            self.root.after(0, lambda detail=detail: self.log_message("Package install failed: " + detail))
            return
        self.root.after(0, lambda: self.log_message("Packages ready. Starting server..."))
        try:
            SERVER_PROCESS = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            threading.Thread(target=self.stream_server_output, daemon=True).start()
            address = lan_address()
            self.root.after(0, lambda address=address: self.log_message(
                "Other computers on this Wi-Fi can open http://" + address + ":8000"
            ))
        except Exception as exc:
            message = str(exc)
            self.root.after(0, lambda message=message: self.log_message("Failed to start server: " + message))

    def stream_server_output(self):
        if not SERVER_PROCESS:
            return
        for line in SERVER_PROCESS.stdout:
            self.root.after(0, lambda l=line: self.log_message("[server] " + l.rstrip()))
        code = SERVER_PROCESS.poll() if SERVER_PROCESS else None
        self.root.after(0, lambda code=code: self.log_message(f"Server stopped (code {code})."))

    def stop_server(self):
        global SERVER_PROCESS
        if SERVER_PROCESS and SERVER_PROCESS.poll() is None:
            SERVER_PROCESS.terminate()
            self.log_message("Server stopped.")
            SERVER_PROCESS = None
        else:
            self.log_message("Server is not running.")

    def open_browser(self):
        webbrowser.open("http://localhost:8000")
        self.log_message("Opened web UI in browser.")

    def save_update_url(self):
        updater.save_url(self.update_url.get())
        self.log_message("Update URL saved. Music, playlists, and the database are never replaced.")

    def check_updates_silent(self):
        threading.Thread(target=self._run_update, args=(False,), daemon=True).start()

    def schedule_updates(self):
        self.check_updates_silent()
        self.root.after(10 * 60 * 1000, self.schedule_updates)

    def check_updates(self):
        updater.save_url(self.update_url.get())
        threading.Thread(target=self._run_update, args=(True,), daemon=True).start()

    def _run_update(self, manual):
        try:
            result = updater.check_and_apply(
                log=lambda m: self.root.after(0, lambda m=m: self.log_message(m))
            )
        except Exception as exc:
            message = str(exc)
            self.root.after(0, lambda message=message: self.log_message("Update failed: " + message))
            return
        def finish():
            if not result.get("ok"):
                if manual:
                    self.log_message("Set an update URL first. Only changed program files are downloaded.")
                return
            changed = [c for c in (result.get("changed") or []) if c.replace("\\", "/") != "VERSION"]
            remote = str(result.get("remote") or "")
            if not changed or (remote and updater.applied_version() == remote):
                updater.mark_applied(remote)
                self.log_message(f"Up to date. Remote {remote or '?'}, this copy {updater.local_version()}.")
                return
            self.log_message("Updated: " + ", ".join(changed))
            self.log_message("Restarting once to use the new version.")
            updater.mark_applied(remote)
            self.stop_server()
            os.environ["MUSICSTREAM_AUTOSTART"] = "1"
            os.execv(sys.executable, [sys.executable, str(BASE_DIR / "desktop_app.py")])
        self.root.after(0, finish)

    def trigger_sort(self):
        self.log_message("Triggering storage sort (hot/cold)...")
        # Could call the API once server is up; for now just log
        self.log_message("(Call /api/sort-storage once the server is running, or it runs automatically hourly.)")

def main():
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    app = MusicStreamApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.stop_server(), root.destroy()))
    root.mainloop()

if __name__ == "__main__":
    main()
