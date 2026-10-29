"""
UI Automation layer for media servers that have no API.
Supports:
- Coordinate-based clicking (user records positions)
- Image recognition (screenshot matching)
- Keyboard typing into focused fields
- Sequences saved as presets

Requires the media server window to be visible and in the foreground when running.
"""

import time
import json
from pathlib import Path
from typing import List, Dict, Optional

try:
    import pyautogui
    pyautogui.FAILSAFE = True  # move mouse to corner to abort
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False

try:
    from pywinauto import Application, Desktop
    HAS_PYWINAUTO = True
except ImportError:
    HAS_PYWINAUTO = False

AUTOMATION_DIR = Path(__file__).parent
SEQUENCES_FILE = AUTOMATION_DIR / "sequences.json"

class AutomationStep:
    def __init__(self, action: str, **kwargs):
        self.action = action  # click, type, wait, hotkey, image_click
        self.params = kwargs

    def to_dict(self):
        return {"action": self.action, **self.params}

    @classmethod
    def from_dict(cls, d):
        action = d.pop("action")
        return cls(action, **d)

class MediaServerAutomator:
    def __init__(self):
        self.sequences = self.load_sequences()

    def load_sequences(self) -> Dict:
        if SEQUENCES_FILE.exists():
            with open(SEQUENCES_FILE) as f:
                return json.load(f)
        return {}

    def save_sequences(self):
        with open(SEQUENCES_FILE, "w") as f:
            json.dump(self.sequences, f, indent=2)

    def record_position(self, name: str) -> tuple:
        """User moves mouse to target, then we capture. Call from UI or console."""
        if not HAS_PYAUTOGUI:
            raise RuntimeError("pyautogui not installed")
        print(f"Move mouse to '{name}' and wait 3 seconds...")
        time.sleep(3)
        pos = pyautogui.position()
        print(f"Captured {name} at {pos}")
        return pos

    def click_at(self, x: int, y: int, clicks: int = 1):
        if not HAS_PYAUTOGUI:
            raise RuntimeError("pyautogui not installed")
        pyautogui.click(x, y, clicks=clicks)
        time.sleep(0.3)

    def type_text(self, text: str, interval: float = 0.05):
        if not HAS_PYAUTOGUI:
            raise RuntimeError("pyautogui not installed")
        pyautogui.write(text, interval=interval)

    def hotkey(self, *keys):
        if not HAS_PYAUTOGUI:
            raise RuntimeError("pyautogui not installed")
        pyautogui.hotkey(*keys)

    def click_image(self, image_path: str, confidence: float = 0.8):
        if not HAS_PYAUTOGUI:
            raise RuntimeError("pyautogui not installed")
        loc = pyautogui.locateCenterOnScreen(image_path, confidence=confidence)
        if loc:
            pyautogui.click(loc)
            return True
        return False

    def run_sequence(self, name: str, query: Optional[str] = None, extra_delay: float = 0.0):
        """Run a saved sequence. extra_delay is added after every step (for slow sites)."""
        if name not in self.sequences:
            raise ValueError(f"Sequence '{name}' not found")
        steps = [AutomationStep.from_dict(s) for s in self.sequences[name]]
        for step in steps:
            if step.action == "click":
                self.click_at(step.params["x"], step.params["y"], step.params.get("clicks", 1))
            elif step.action == "type":
                text = query if step.params.get("use_query") else step.params.get("text", "")
                self.type_text(text)
            elif step.action == "hotkey":
                self.hotkey(*step.params["keys"])
            elif step.action == "wait":
                time.sleep(step.params.get("seconds", 1) + extra_delay)
            elif step.action == "image_click":
                self.click_image(step.params["image"])
            elif step.action == "type_query":
                if query:
                    self.type_text(query)
            elif step.action == "delay":
                time.sleep(step.params.get("seconds", 1) + extra_delay)
            time.sleep(0.2 + extra_delay)
        return {"status": "ran", "sequence": name, "query": query, "extra_delay": extra_delay}

    def save_sequence(self, name: str, steps: List[dict]):
        self.sequences[name] = steps
        self.save_sequences()

# Example sequence structure:
# {
#   "search_media_server": [
#     {"action": "click", "x": 500, "y": 100},          # click search box
#     {"action": "hotkey", "keys": ["ctrl", "a"]},      # select all
#     {"action": "type_query"},                         # type the search term
#     {"action": "hotkey", "keys": ["enter"]},          # search
#     {"action": "wait", "seconds": 2}
#   ]
# }
