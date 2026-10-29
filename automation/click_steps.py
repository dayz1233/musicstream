"""Record real mouse and keyboard steps, then play them back.

Chrome itself is a normal window. Nothing is automated inside the browser.
"""

import json
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CONFIG = BASE / "data" / "click_steps.json"


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

    def open_chrome(self, url: str) -> dict:
        url = (url or "").strip()
        if url in ("http://", "https://"):
            url = ""
        if url and not url.startswith("http"):
            url = "https://" + url
        if url:
            self.url = url
            self._save()
        chrome = _chrome_path()
        if chrome:
            subprocess.Popen([chrome, url] if url else [chrome])
        elif url:
            webbrowser.open(url)
        else:
            webbrowser.open("about:blank")
        self.start()
        return {"ok": True, "url": self.url, "recording": True}

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
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.15
        typer = Controller()
        query = (query or "").strip()
        for step in steps:
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
        return {"ok": True, "played": len(steps)}


click_steps = ClickSteps()
