"""Notice keys, clicks, and the scroll wheel without taking them away.

Nothing here is a keyboard hook. The website still receives every key,
including Tab and Page Down. The mouse hook forwards the event first.
"""

import ctypes
import json
import sys
import threading
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t

NAMED = {
    0x09: "tab",
    0x0D: "enter",
    0x21: "page_up",
    0x22: "page_down",
    0x08: "backspace",
    0x20: "space",
    0x25: "left",
    0x26: "up",
    0x27: "right",
    0x28: "down",
    0x23: "end",
    0x24: "home",
    0x2E: "delete",
    0x1B: "esc",
}


def emit(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def pressed(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def poll_keys():
    held = set()
    while True:
        shift = pressed(0x10)
        seen = set()
        for vk, name in NAMED.items():
            if pressed(vk):
                seen.add(vk)
                if vk not in held:
                    emit({"type": "key", "key": name, "char": " " if name == "space" else ""})
        for vk in list(range(0x30, 0x3A)) + list(range(0x41, 0x5B)):
            if not pressed(vk):
                continue
            seen.add(vk)
            if vk in held:
                continue
            if 0x41 <= vk <= 0x5A:
                char = chr(vk if shift else vk + 32)
            else:
                char = chr(vk)
            emit({"type": "key", "key": "", "char": char})
        held = seen
        time.sleep(0.03)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK


def main():
    threading.Thread(target=poll_keys, daemon=True).start()
    if sys.platform != "win32":
        while True:
            time.sleep(1)

    def forward(code, wparam, lparam):
        copied = None
        if code >= 0:
            info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if wparam == 0x0201:
                copied = {"type": "click", "x": int(info.pt.x), "y": int(info.pt.y)}
            elif wparam == 0x020A:
                delta = ctypes.c_short((info.mouseData >> 16) & 0xFFFF).value
                if delta:
                    copied = {"type": "scroll", "x": int(info.pt.x), "y": int(info.pt.y), "dx": 0, "dy": 1 if delta > 0 else -1}
        result = user32.CallNextHookEx(None, code, wparam, lparam)
        if copied:
            emit(copied)
        return result

    callback = HOOKPROC(forward)
    main.callback = callback
    hook = user32.SetWindowsHookExW(14, callback, None, 0)
    if not hook:
        while True:
            time.sleep(1)
    message = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(message), 0, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))


if __name__ == "__main__":
    main()
