"""Listen for keys without blocking them.

This runs in its own process so a busy music server cannot freeze Tab.
"""

import json
import sys

from pynput import keyboard


def main():
    def on_press(key):
        char = getattr(key, "char", None)
        name = getattr(key, "name", None)
        payload = {"char": char or "", "key": name or ""}
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()

    with keyboard.Listener(on_press=on_press, suppress=False) as listener:
        listener.join()


if __name__ == "__main__":
    main()
