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

    def _save(self):
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps({"url": self.url, "steps": self.steps}, indent=2), encoding="utf-8")

    def status(self) -> dict:
        with self.lock:
            return {
                "ok": True,
                "recording": self.recording,
                "url": self.url,
                "steps": [self._public_step(index, step) for index, step in enumerate(self.steps, start=1)],
                "message": self.message,
                "next": self.next_message,
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
        return {"step": number, "action": label}

    def note(self, message: str, nxt: str = ""):
        with self.lock:
            self.message = message
            self.next_message = nxt

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
        hwnd = self._hwnd
        self._hwnd = None
        if hwnd and sys.platform == "win32":
            ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
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
            self.steps.append({"action": "click", "x": int(x), "y": int(y), "t": time.time()})
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
            self.steps.append({"action": "scroll", "x": int(x), "y": int(y), "dy": int(dy), "t": now})
            count = len(self.steps)
        self.note(f"Recorded step {count}: scroll.", "I think you are lining the page up. Do the next action, or press Stop.")

    def _on_key(self, key):
        if not self.recording:
            return
        from pynput.keyboard import Key
        if key == Key.enter:
            self._flush_typing()
            with self.lock:
                self.steps.append({"action": "key", "key": "enter", "t": time.time()})
            return
        if key == Key.tab:
            self._flush_typing()
            with self.lock:
                self.steps.append({"action": "key", "key": "tab", "t": time.time()})
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
            self.steps.append({"action": "type", "text": text, "search": search, "t": time.time()})
        self.note("Recorded typing in the search bar." if search else "Recorded typing.", "Next action will be counted as the following step.")

    def replay(self, query: str, delay: float = 0.8) -> dict:
        if self.recording:
            self.stop()
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
        self._launch(self.url)
        time.sleep(max(1.5, delay))
        query = (query or "").strip()
        typer = Controller()
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.15
        for index, step in enumerate(steps):
            upcoming = labels[index + 1] if index + 1 < len(labels) else "watch the Downloads folder, then close Chrome"
            self.note(f"Doing step {index + 1} of {len(steps)}: {labels[index]}.", f"Next: {upcoming}.")
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
            time.sleep(max(0.2, delay))
        self.note("The clicks are finished.", "Next: wait until the file finishes in Downloads, then close Chrome.")
        return {"ok": True, "played": len(steps)}


click_steps = ClickSteps()
