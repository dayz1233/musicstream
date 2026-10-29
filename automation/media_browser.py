"""Private Chrome window for a media server that has no API.

Clicks and typing stay inside that window. The computer's real mouse is not used.
"""

import json
import queue
import re
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PROFILE = BASE / "data" / "chrome-media-profile"
CONFIG = BASE / "data" / "media_browser.json"


def clean_text(value: str) -> str:
    text = (value or "").replace("’", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text).strip(" -")
    return text


def core_title(title: str) -> str:
    text = clean_text(title)
    text = re.sub(r"\s*[\(\[][^)\]]*(?:remaster|feat|ft\.|live|mono|stereo|edit|version)[^)\]]*[\)\]]", "", text, flags=re.I)
    text = re.sub(r"\s+-\s+\d{4}\s+remaster.*$", "", text, flags=re.I)
    return clean_text(text)


def search_variants(title: str, artist: str) -> list:
    title = clean_text(title)
    artist = clean_text((artist or "").split(";")[0])
    short = core_title(title)
    variants = []
    if artist and short:
        variants.append(f"{artist} {short}")
    if short:
        variants.append(short)
    if artist and title and title.lower() != short.lower():
        variants.append(f"{artist} {title}")
    if title and title.lower() != short.lower():
        variants.append(title)
    seen = set()
    ordered = []
    for item in variants:
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            ordered.append(item)
    return ordered


class MediaBrowser:
    def __init__(self):
        self._commands = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="media-browser", daemon=True)
            self._thread.start()

    def call(self, action: str, payload=None, timeout: int = 120) -> dict:
        self.start()
        box = queue.Queue(1)
        self._commands.put((action, payload or {}, box))
        try:
            return box.get(timeout=timeout)
        except queue.Empty:
            return {"ok": False, "error": "The media browser took too long."}

    def _loop(self):
        playwright = context = None
        while True:
            action, payload, box = self._commands.get()
            try:
                if action == "status":
                    box.put(self._status(context))
                    continue
                playwright, context = self._ensure(playwright, context)
                page = context.pages[0] if context.pages else context.new_page()
                if action == "open":
                    box.put(self._open(page, payload.get("url") or ""))
                elif action == "arm":
                    box.put(self._arm(page))
                elif action == "search":
                    box.put(self._search(page, payload))
                else:
                    box.put({"ok": False, "error": f"Unknown action {action}"})
            except Exception as exc:
                context = None
                box.put({"ok": False, "error": str(exc)})

    def _ensure(self, playwright, context):
        if context is not None:
            return playwright, context
        from playwright.sync_api import sync_playwright
        PROFILE.mkdir(parents=True, exist_ok=True)
        playwright = sync_playwright().start()
        downloads = Path.home() / "Downloads"
        downloads.mkdir(exist_ok=True)
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE),
            channel="chrome",
            headless=False,
            accept_downloads=True,
            downloads_path=str(downloads),
            viewport={"width": 1200, "height": 800},
            args=["--disable-blink-features=AutomationControlled"],
        )
        if not context.pages:
            context.new_page()
        return playwright, context

    def _config(self) -> dict:
        if CONFIG.is_file():
            try:
                return json.loads(CONFIG.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_config(self, data: dict):
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _status(self, context) -> dict:
        config = self._config()
        return {
            "ok": True,
            "open": context is not None,
            "url": config.get("url") or "",
            "ready": bool(config.get("selector")),
        }

    def _open(self, page, url: str) -> dict:
        config = self._config()
        url = (url or config.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "Enter the media server address first."}
        if not url.startswith("http"):
            url = "https://" + url
        config["url"] = url
        self._save_config(config)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        return {"ok": True, "url": url, "ready": bool(config.get("selector"))}

    def _arm(self, page) -> dict:
        selector = page.evaluate(
            """() => new Promise((resolve) => {
                const once = (event) => {
                    const target = event.target;
                    const el = target.closest('input, textarea, [contenteditable="true"]') || target;
                    let found = '';
                    if (el.id) found = '#' + CSS.escape(el.id);
                    else if (el.getAttribute('name')) found = el.tagName.toLowerCase() + '[name="' + el.getAttribute('name').replaceAll('"', '') + '"]';
                    else if (el.getAttribute('placeholder')) found = el.tagName.toLowerCase() + '[placeholder="' + el.getAttribute('placeholder').replaceAll('"', '') + '"]';
                    else if (el.getAttribute('aria-label')) found = el.tagName.toLowerCase() + '[aria-label="' + el.getAttribute('aria-label').replaceAll('"', '') + '"]';
                    else {
                        el.setAttribute('data-musicstream-search', '1');
                        found = '[data-musicstream-search="1"]';
                    }
                    resolve(found);
                };
                document.addEventListener('click', once, {capture: true, once: true});
                setTimeout(() => resolve(''), 60000);
            })"""
        )
        if not selector:
            return {"ok": False, "error": "The search box was not clicked in time."}
        config = self._config()
        config["selector"] = selector
        self._save_config(config)
        return {"ok": True, "selector": selector}

    def _search(self, page, payload: dict) -> dict:
        config = self._config()
        selector = config.get("selector") or ""
        if not selector:
            return {"ok": False, "error": "Click the search box in the media browser once so it can be remembered."}
        title = payload.get("title") or ""
        artist = payload.get("artist") or ""
        delay = float(payload.get("delay") or 1.5)
        attempts = []
        box = page.locator(selector).first
        for query in search_variants(title, artist):
            box.click(timeout=8000)
            box.fill("")
            box.press_sequentially(query, delay=35)
            page.wait_for_timeout(int(delay * 1000))
            box.press("Enter")
            page.wait_for_timeout(int(max(delay, 1.2) * 1000))
            match = self._best_result(page, title, artist)
            attempts.append({"query": query, "match": match["text"] if match else ""})
            if match and match["score"] >= 5:
                match["handle"].click(timeout=8000)
                return {"ok": True, "query": query, "matched": match["text"], "attempts": attempts}
        return {"ok": False, "error": "No result matched that song.", "attempts": attempts}

    def _best_result(self, page, title: str, artist: str):
        wanted_title = core_title(title).lower()
        wanted_artist = clean_text((artist or "").split(";")[0]).lower()
        best = None
        for handle in page.locator("a, button, [role='link'], [role='button'], tr, li").all()[:100]:
            try:
                text = " ".join(handle.inner_text(timeout=400).split())
            except Exception:
                continue
            if not text or len(text) > 300:
                continue
            low = text.lower()
            if "data-musicstream-search" in low:
                continue
            score = 0
            if wanted_title and wanted_title in low:
                score += 5
            if wanted_artist and wanted_artist in low:
                score += 3
            if score > (best["score"] if best else 0):
                best = {"score": score, "text": text[:180], "handle": handle}
        return best


media_browser = MediaBrowser()
