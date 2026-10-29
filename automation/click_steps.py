"""Record real mouse and keyboard steps, then play them back.

Chrome itself is a normal window. Nothing is automated inside the browser.
"""

import json
import subprocess
import sys
import threading
import time
import webbrowser
import ctypes
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CONFIG = BASE / "data" / "click_steps.json"
PROFILE = BASE / "data" / "chrome-media-window"
DEBUG_PORT = 9333
IDLE_SECONDS = 20


class TrafficWatch:
    """Watch the media Chrome window. Background noise does not count as work."""

    BACKGROUND = (
        "clients2.google", "safebrowsing", "gstatic.com", "google-analytics",
        "doubleclick", "googletagmanager", "update.googleapis", "favicon",
        "chrome-extension://", "generate_204", "connectivitycheck",
        "msftconnecttest", "detectportal", "clients.google",
    )

    def __init__(self):
        self.last_real = time.time()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self.stop()
        self.last_real = time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="media-traffic", daemon=True)
        self._thread.start()

    def stop(self):
        if self._stop:
            self._stop.set()

    def idle(self, seconds: float = IDLE_SECONDS) -> bool:
        if self._download_moving():
            self.last_real = time.time()
            return False
        return time.time() - self.last_real >= seconds

    def _download_moving(self) -> bool:
        folder = Path.home() / "Downloads"
        now = time.time()
        try:
            for path in folder.glob("*"):
                if path.suffix.lower() in {".crdownload", ".tmp", ".part"} and now - path.stat().st_mtime < 4:
                    return True
        except OSError:
            return False
        return False

    def _background(self, url: str) -> bool:
        low = (url or "").lower()
        return any(part in low for part in self.BACKGROUND)

    def _run(self):
        while not self._stop.is_set():
            try:
                import asyncio
                asyncio.run(self._listen())
            except Exception:
                if self._stop.is_set():
                    return
                time.sleep(0.5)

    async def _listen(self):
        import asyncio
        import urllib.request
        import websockets
        ws_url = ""
        while not self._stop.is_set() and not ws_url:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json", timeout=2) as resp:
                    tabs = json.loads(resp.read().decode())
                for tab in tabs:
                    if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"):
                        ws_url = tab["webSocketDebuggerUrl"]
                        break
            except Exception:
                await asyncio.sleep(0.4)
        if not ws_url:
            return
        async with websockets.connect(ws_url, max_size=2_000_000) as socket:
            await socket.send(json.dumps({"id": 1, "method": "Network.enable"}))
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=2)
                except asyncio.TimeoutError:
                    continue
                message = json.loads(raw)
                if message.get("method") != "Network.requestWillBeSent":
                    continue
                url = message.get("params", {}).get("request", {}).get("url", "")
                if url and not self._background(url):
                    self.last_real = time.time()


def _chrome_path() -> str:
    if sys.platform != "win32":
        return ""
    candidates = [
        Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return ""


class ClickSteps:
    def __init__(self):
        self.lock = threading.Lock()
        self.steps = []
        self.recording = False
        self.url = ""
        self._mouse = None
        self._keys = None
        self._typing = ""
        self._typing_search = False
        self._timer = None
        self._hwnd = None
        self.message = ""
        self.next_message = ""
        self.delay = 0.8
        self.gaps = dict(DEFAULT_GAPS)
        self.traffic = TrafficWatch()
        self.abort = False
        self._load()

    def _load(self):
        if not CONFIG.is_file():
            return
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        self.steps = data.get("steps") or []
        self.url = data.get("url") or ""
        try:
            self.delay = min(30.0, max(0.0, float(data.get("delay", 0.8))))
        except (TypeError, ValueError):
            self.delay = 0.8
        saved = data.get("gaps") or {}
        if not saved and data.get("delay") is not None:
            saved = {"click": self.delay, "scroll": self.delay, "type": self.delay, "key": self.delay}
        for name, default in DEFAULT_GAPS.items():
            try:
                limit = 600.0 if name == "download" else 30.0
                self.gaps[name] = min(limit, max(0.0, float(saved.get(name, default))))
            except (TypeError, ValueError):
                self.gaps[name] = default

    def _save(self):
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps({"url": self.url, "steps": self.steps, "delay": self.delay, "gaps": self.gaps}, indent=2), encoding="utf-8")

    def status(self) -> dict:
        with self.lock:
            return {
                "ok": True,
                "recording": self.recording,
                "url": self.url,
                "steps": [self._public_step(index, step) for index, step in enumerate(self.steps, start=1)],
                "message": self.message,
                "next": self.next_message,
                "delay": self.delay,
                "gaps": dict(self.gaps),
            }

    def _public_step(self, number, step) -> dict:
        action = step.get("action")
        if action == "type" and step.get("search"):
            label = "search bar"
        elif action == "type":
            label = "type"
        elif action == "scroll":
            direction = "up" if int(step.get("dy") or 0) > 0 else "down"
            label = f"scroll {direction}"
        elif action == "key":
            label = step.get("key") or "key"
        else:
            label = "click"
        name = (step.get("name") or "").strip()
        if name:
            label = name
        return {"step": number, "action": label, "kind": step.get("action") or "click", "wait": round(float(step.get("wait", 0.4)), 2), "name": name}

    def note(self, message: str, nxt: str = ""):
        with self.lock:
            self.message = message
            self.next_message = nxt

    def set_delay(self, seconds: float) -> dict:
        try:
            seconds = min(30.0, max(0.0, float(seconds)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Enter a number of seconds."}
        with self.lock:
            self.delay = seconds
        self._save()
        self.note(f"Wait between actions is {seconds:g} seconds.", "")
        return self.status()

    def set_gap(self, action: str, seconds: float) -> dict:
        action = (action or "").strip().lower()
        if action not in DEFAULT_GAPS:
            return {"ok": False, "error": "Unknown action."}
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Enter a number of seconds."}
        limit = 600.0 if action == "download" else 30.0
        seconds = min(limit, max(0.0, seconds))
        with self.lock:
            self.gaps[action] = seconds
        self._save()
        self.note(f"{action} wait is {seconds:g} seconds.", "")
        return self.status()

    def gap_for(self, action: str) -> float:
        with self.lock:
            return float(self.gaps.get(action, DEFAULT_GAPS.get(action, 0.4)))

    def _add_step(self, step):
        step["t"] = time.time()
        step.setdefault("name", "")
        step.setdefault("wait", 0.4)
        if self.steps:
            previous = self.steps[-1]
            previous["wait"] = round(max(0.0, step["t"] - float(previous.get("t") or step["t"])), 2)
        self.steps.append(step)

    def edit_steps(self, indexes, wait=None, name=None) -> dict:
        updated = 0
        try:
            chosen = [int(part) for part in str(indexes or "").split(",") if part.strip()]
        except ValueError:
            return {"ok": False, "error": "Select steps from the list."}
        new_wait = None
        if wait not in (None, ""):
            try:
                new_wait = min(120.0, max(0.0, float(wait)))
            except (TypeError, ValueError):
                return {"ok": False, "error": "Enter a number of seconds."}
        with self.lock:
            for number in chosen:
                if number < 1 or number > len(self.steps):
                    continue
                step = self.steps[number - 1]
                if new_wait is not None:
                    step["wait"] = new_wait
                if name:
                    step["name"] = str(name).strip()
                updated += 1
        if not updated:
            return {"ok": False, "error": "Select one or more steps first."}
        self._save()
        self.note(f"Updated {updated} steps.", "")
        return self.status()

    def estop(self) -> dict:
        self.abort = True
        if self.recording:
            self.stop()
        self.close_window()
        self.note("E-stop. Recording and playback are stopped.", "")
        return self.status()

    def start_test(self, query: str) -> dict:
        if self.recording:
            self.stop()
        self.abort = False
        self.note("Test started.", "Press E-stop to stop it.")

        def run():
            result = self.replay((query or "").strip() or "test")
            if self.abort:
                return
            if result.get("ok"):
                self.note("Test finished. The window is still open.", "Press E-stop to close it.")
            else:
                self.note(result.get("error") or "The test did not finish.", "")

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True, "message": "Test started."}

    def _pause(self, seconds: float) -> bool:
        deadline = time.time() + max(0.0, float(seconds))
        while time.time() < deadline:
            if self.abort:
                return False
            time.sleep(0.05)
        return not self.abort

    def _hwnds(self):
        if sys.platform != "win32":
            return []
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            if class_name.value != "Chrome_WidgetWin_1":
                return True
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, 512)
            if title.value:
                found.append(hwnd)
            return True

        user32.EnumWindows(callback, 0)
        return found

    def _focus(self):
        hwnd = self._hwnd
        if not hwnd or sys.platform != "win32":
            return
        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd):
            return
        user32.ShowWindow(hwnd, 9)
        foreground = user32.GetForegroundWindow()
        current = ctypes.windll.kernel32.GetCurrentThreadId()
        target = user32.GetWindowThreadProcessId(hwnd, None)
        previous = user32.GetWindowThreadProcessId(foreground, None)
        user32.AttachThreadInput(current, previous, True)
        user32.AttachThreadInput(current, target, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(current, previous, False)
        user32.AttachThreadInput(current, target, False)

    def _launch(self, url: str):
        chrome = _chrome_path()
        PROFILE.mkdir(parents=True, exist_ok=True)
        before = set(self._hwnds())
        if chrome:
            command = [
                chrome,
                f"--user-data-dir={PROFILE}",
                "--new-window",
                "--disable-extensions",
                "--no-first-run",
                "--disable-session-crashed-bubble",
                "--window-position=40,40",
                "--window-size=1280,800",
                f"--remote-debugging-port={DEBUG_PORT}",
            ]
            if url:
                command.append(url)
            subprocess.Popen(command)
        elif url:
            webbrowser.open(url)
        else:
            webbrowser.open("about:blank")
        deadline = time.time() + 8
        while time.time() < deadline:
            fresh = [hwnd for hwnd in self._hwnds() if hwnd not in before]
            if fresh:
                self._hwnd = fresh[-1]
                self._focus()
                return
            time.sleep(0.2)

    def close_window(self):
        self.traffic.stop()
        hwnd = self._hwnd
        self._hwnd = None
        if hwnd and sys.platform == "win32":
            ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
        if not self.abort:
            self.note("The download finished.", "The media Chrome window is closing.")

    def open_chrome(self, url: str) -> dict:
        url = (url or "").strip()
        if url in ("http://", "https://"):
            url = ""
        if url and not url.startswith("http"):
            url = "https://" + url
        if url:
            self.url = url
            self._save()
        self.note("Opening a fresh Chrome window.", "Next: click, scroll, or type. The first thing you type is the search bar.")
        self._launch(self.url)
        self.start()
        return self.status()

    def start(self) -> dict:
        if self.recording:
            return self.status()
        try:
            from pynput import keyboard, mouse
        except ImportError:
            return {"ok": False, "error": "pynput is not installed yet. Restart MusicStream so it can install."}
        self.recording = True
        self.abort = False
        self.note("RECORDING. Click through the site. Press Stop when you are done.", "")
        self._mouse = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
        self._keys = keyboard.Listener(on_press=self._on_key)
        self._mouse.start()
        self._keys.start()
        return self.status()

    def stop(self) -> dict:
        self._flush_typing()
        self.recording = False
        for listener in (self._mouse, self._keys):
            try:
                if listener:
                    listener.stop()
            except Exception:
                pass
        self._mouse = None
        self._keys = None
        with self.lock:
            if self.steps and self.steps[-1].get("action") == "click" and time.time() - self.steps[-1].get("t", 0) < 1:
                self.steps.pop()
        self._save()
        return self.status()

    def clear(self) -> dict:
        with self.lock:
            self.steps = []
        self._save()
        return self.status()

    def _on_click(self, x, y, button, pressed):
        if not self.recording or not pressed:
            return
        self._flush_typing()
        with self.lock:
            self._add_step({"action": "click", "x": int(x), "y": int(y)})
            count = len(self.steps)
        self.note(f"Recorded step {count}: click.", "I think you are clicking through the site. Do the next action, or press Stop.")

    def _on_scroll(self, x, y, dx, dy):
        if not self.recording:
            return
        self._flush_typing()
        with self.lock:
            now = time.time()
            if self.steps and self.steps[-1].get("action") == "scroll" and now - self.steps[-1].get("t", 0) < 0.45:
                self.steps[-1]["dy"] = int(self.steps[-1].get("dy", 0)) + int(dy)
                self.steps[-1]["t"] = now
                return
            self._add_step({"action": "scroll", "x": int(x), "y": int(y), "dy": int(dy)})
            count = len(self.steps)
        self.note(f"Recorded step {count}: scroll.", "I think you are lining the page up. Do the next action, or press Stop.")

    def _on_key(self, key):
        if not self.recording:
            return
        from pynput.keyboard import Key
        if key == Key.enter:
            self._flush_typing()
            with self.lock:
                self._add_step({"action": "key", "key": "enter"})
            return
        if key == Key.tab:
            self._flush_typing()
            with self.lock:
                self._add_step({"action": "key", "key": "tab"})
            return
        if key == Key.backspace:
            self._typing = self._typing[:-1]
            self._arm_flush()
            return
        char = getattr(key, "char", None)
        if not char:
            return
        if not self._typing:
            with self.lock:
                self._typing_search = not any(step.get("action") == "type" and step.get("search") for step in self.steps)
        self._typing += char
        self._arm_flush()

    def _arm_flush(self):
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(1.0, self._flush_typing)
        self._timer.daemon = True
        self._timer.start()

    def _flush_typing(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        text = self._typing
        search = self._typing_search
        self._typing = ""
        if not text:
            return
        with self.lock:
            if self.steps and self.steps[-1].get("action") == "type":
                self.steps[-1]["text"] = (self.steps[-1].get("text") or "") + text
                self.steps[-1]["search"] = True
                self.steps[-1]["t"] = time.time()
                return
            self.steps.append({"action": "type", "text": text, "search": search, "t": time.time(), "name": "", "wait": 0.4})
            if len(self.steps) >= 2:
                previous = self.steps[-2]
                previous["wait"] = round(max(0.0, self.steps[-1]["t"] - float(previous.get("t") or self.steps[-1]["t"])), 2)
        self.note("Recorded typing in the search bar." if search else "Recorded typing.", "Next action will be counted as the following step.")

    def replay(self, query: str, delay: float = None) -> dict:
        if self.recording:
            self.stop()
        if delay is None:
            delay = self.delay
        delay = min(30.0, max(0.0, float(delay)))
        with self.lock:
            steps = list(self.steps)
        if not steps:
            return {"ok": False, "error": "No steps recorded yet. Open the media browser and click through the site once."}
        try:
            import pyautogui
            from pynput.keyboard import Controller
        except ImportError as exc:
            return {"ok": False, "error": str(exc)}
        labels = [self._public_step(index, step)["action"] for index, step in enumerate(steps, start=1)]
        self.note("Opening a fresh Chrome window on the site.", f"Next: {labels[0]}.")
        self.abort = False
        self.traffic.start()
        self._launch(self.url)
        if not self._pause(self.gap_for("open")):
            self.close_window()
            return {"ok": False, "error": "E-stop"}
        query = (query or "").strip()
        typer = Controller()
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.15
        for index, step in enumerate(steps):
            upcoming = labels[index + 1] if index + 1 < len(labels) else "watch the Downloads folder, then close Chrome"
            self.note(f"Doing step {index + 1} of {len(steps)}: {labels[index]}.", f"Next: {upcoming}.")
            if self.abort:
                self.note("E-stop.", "Stopped.")
                self.close_window()
                return {"ok": False, "error": "E-stop"}
            if self.idle():
                self.note("The page stopped doing anything besides background traffic.", "Closing this window and trying again.")
                return {"ok": False, "retry": True, "error": "The page went idle."}
            self._focus()
            action = step.get("action")
            if action == "click":
                pyautogui.click(int(step["x"]), int(step["y"]))
            elif action == "scroll":
                pyautogui.moveTo(int(step["x"]), int(step["y"]))
                pyautogui.scroll(int(step.get("dy") or 0))
            elif action == "type":
                text = query if step.get("search") else (step.get("text") or "")
                pyautogui.hotkey("ctrl", "a")
                time.sleep(0.1)
                typer.type(text)
            elif action == "key":
                pyautogui.press(step.get("key") or "enter")
            if not self._pause(step.get("wait", 0.4)):
                self.note("E-stop.", "Stopped.")
                self.close_window()
                return {"ok": False, "error": "E-stop"}
        self.note("The clicks are finished.", "Next: wait until the file finishes in Downloads, then close Chrome.")
        return {"ok": True, "played": len(steps)}

    def idle(self, seconds: float = IDLE_SECONDS) -> bool:
        return self.traffic.idle(seconds)


click_steps = ClickSteps()
