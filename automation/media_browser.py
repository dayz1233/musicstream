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
                page = self._page(context)
                if action == "open":
                    box.put(self._open(page, payload.get("url") or ""))
                elif action == "arm":
                    box.put(self._arm(page))
                elif action == "search":
                    box.put(self._search(page, payload))
                elif action == "record-click":
                    box.put(self._record_click(page))
                elif action == "add-step":
                    box.put(self._add_step(payload))
                elif action == "save-sequence":
                    box.put(self._save_sequence(payload.get("name") or ""))
                elif action == "run-sequence":
                    box.put(self._run_sequence(page, payload))
                elif action == "record":
                    box.put(self._record_part(page, payload.get("part") or ""))
                elif action == "clear":
                    box.put(self._clear_recipe())
                elif action == "fetch":
                    box.put(self._fetch(page, payload))
                else:
                    box.put({"ok": False, "error": f"Unknown action {action}"})
            except Exception as exc:
                if not self._alive(context):
                    self._shutdown(playwright, context)
                    playwright = context = None
                box.put({"ok": False, "error": str(exc)})

    def _alive(self, context) -> bool:
        if context is None:
            return False
        try:
            context.pages
            return True
        except Exception:
            return False

    def _shutdown(self, playwright, context):
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            if playwright is not None:
                playwright.stop()
        except Exception:
            pass
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                (PROFILE / name).unlink()
            except OSError:
                pass

    def _page(self, context):
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.bring_to_front()
        except Exception:
            pass
        return page

    def _ensure(self, playwright, context):
        if self._alive(context):
            return playwright, context
        self._shutdown(playwright, context)
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
            no_viewport=True,
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
            "open": self._alive(context),
            "url": config.get("url") or "",
            "ready": bool(config.get("selector")),
            "draft": config.get("draft") or [],
            "sequences": list((config.get("sequences") or {}).keys()),
            "recipe": self._recipe_status(config.get("recipe") or {}),
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

    def _capture_selector(self, page) -> str:
        return page.evaluate(
            """() => new Promise((resolve) => {
                const once = (event) => {
                    const target = event.target;
                    const el = target.closest('input, textarea, button, a, [role="button"], [contenteditable="true"]') || target;
                    let found = '';
                    if (el.id) found = '#' + CSS.escape(el.id);
                    else if (el.getAttribute('name')) found = el.tagName.toLowerCase() + '[name="' + el.getAttribute('name').replaceAll('"', '') + '"]';
                    else if (el.getAttribute('placeholder')) found = el.tagName.toLowerCase() + '[placeholder="' + el.getAttribute('placeholder').replaceAll('"', '') + '"]';
                    else if (el.getAttribute('aria-label')) found = el.tagName.toLowerCase() + '[aria-label="' + el.getAttribute('aria-label').replaceAll('"', '') + '"]';
                    else {
                        const mark = 'ms-' + Date.now();
                        el.setAttribute('data-musicstream-step', mark);
                        found = '[data-musicstream-step="' + mark + '"]';
                    }
                    resolve(found);
                };
                document.addEventListener('click', once, {capture: true, once: true});
                setTimeout(() => resolve(''), 60000);
            })"""
        )

    def _record_click(self, page) -> dict:
        selector = self._capture_selector(page)
        if not selector:
            return {"ok": False, "error": "No click was made in the media Chrome window."}
        config = self._config()
        draft = config.get("draft") or []
        draft.append({"action": "click", "selector": selector})
        config["draft"] = draft
        self._save_config(config)
        return {"ok": True, "draft": draft}

    def _add_step(self, payload: dict) -> dict:
        action = payload.get("action") or ""
        if action not in ("type", "key", "wait"):
            return {"ok": False, "error": "Step must be type, key, or wait."}
        step = {"action": action}
        if action == "type":
            step["text"] = payload.get("text") or "{query}"
        elif action == "key":
            step["key"] = payload.get("key") or "Enter"
        else:
            step["seconds"] = float(payload.get("seconds") or 1)
        config = self._config()
        draft = config.get("draft") or []
        draft.append(step)
        config["draft"] = draft
        self._save_config(config)
        return {"ok": True, "draft": draft}

    def _save_sequence(self, name: str) -> dict:
        name = name.strip()
        config = self._config()
        draft = config.get("draft") or []
        if not name or not draft:
            return {"ok": False, "error": "Give the sequence a name after adding at least one step."}
        sequences = config.get("sequences") or {}
        sequences[name] = draft
        config["sequences"] = sequences
        config["draft"] = []
        self._save_config(config)
        return {"ok": True, "saved": name, "steps": draft}

    def _run_sequence(self, page, payload: dict) -> dict:
        config = self._config()
        name = (payload.get("name") or "").strip()
        steps = (config.get("sequences") or {}).get(name)
        if not steps:
            return {"ok": False, "error": "That sequence is not saved."}
        title = clean_text(payload.get("title") or "")
        artist = clean_text((payload.get("artist") or "").split(";")[0])
        query = payload.get("query") or (f"{artist} {core_title(title)}".strip())
        for step in steps:
            action = step.get("action")
            if action == "click":
                page.locator(step["selector"]).first.click(timeout=8000)
            elif action == "type":
                text = (step.get("text") or "").replace("{query}", query).replace("{title}", title).replace("{artist}", artist)
                page.keyboard.type(text, delay=35)
            elif action == "key":
                page.keyboard.press(step.get("key") or "Enter")
            elif action == "wait":
                page.wait_for_timeout(int(float(step.get("seconds") or 1) * 1000))
        return {"ok": True, "ran": name, "query": query}

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

    def _recipe_status(self, recipe: dict) -> dict:
        return {
            "search_field": bool(recipe.get("search_field")),
            "search_button": bool(recipe.get("search_button")),
            "results": bool(recipe.get("results")),
            "result": bool(recipe.get("result")),
            "download": bool(recipe.get("download")),
            "extra": len(recipe.get("extra") or []),
        }

    def _capture_part(self, page, as_row: bool) -> str:
        return page.evaluate(
            """(asRow) => new Promise((resolve) => {
                const once = (event) => {
                    const target = event.target;
                    const el = asRow
                        ? (target.closest('li, tr, article, [role="listitem"], [role="row"], a') || target)
                        : (target.closest('input, textarea, button, a, [role="button"], [contenteditable="true"]') || target);
                    let found = '';
                    if (!asRow && el.id) found = '#' + CSS.escape(el.id);
                    else if (!asRow && el.getAttribute('name')) found = el.tagName.toLowerCase() + '[name="' + el.getAttribute('name').replaceAll('"', '') + '"]';
                    else if (!asRow && el.getAttribute('placeholder')) found = el.tagName.toLowerCase() + '[placeholder="' + el.getAttribute('placeholder').replaceAll('"', '') + '"]';
                    else if (el.classList && el.classList.length) {
                        const name = [...el.classList].find(item => !/\\d{4,}/.test(item));
                        if (name) found = el.tagName.toLowerCase() + '.' + CSS.escape(name);
                    }
                    if (!found) {
                        const mark = 'ms-' + Date.now();
                        el.setAttribute('data-musicstream-step', mark);
                        found = '[data-musicstream-step="' + mark + '"]';
                    }
                    resolve(found);
                };
                document.addEventListener('click', once, {capture: true, once: true});
                setTimeout(() => resolve(''), 60000);
            })""",
            as_row,
        )

    def _record_part(self, page, part: str) -> dict:
        allowed = {"search_field", "search_button", "results", "result", "download", "extra"}
        if part not in allowed:
            return {"ok": False, "error": "Unknown recording target."}
        selector = self._capture_part(page, part in {"results", "result"})
        if not selector:
            return {"ok": False, "error": "Nothing was clicked in the media Chrome window."}
        config = self._config()
        recipe = config.get("recipe") or {}
        if part == "extra":
            recipe.setdefault("extra", []).append(selector)
        else:
            recipe[part] = selector
        config["recipe"] = recipe
        self._save_config(config)
        return {"ok": True, "part": part, "recipe": self._recipe_status(recipe)}

    def _clear_recipe(self) -> dict:
        config = self._config()
        config["recipe"] = {}
        self._save_config(config)
        return {"ok": True, "recipe": self._recipe_status({})}

    def _click_match(self, page, selector: str, title: str, artist: str):
        wanted_title = core_title(title).lower()
        wanted_artist = clean_text((artist or "").split(";")[0]).lower()
        query = f"{wanted_artist} {wanted_title}".strip()
        best = None
        for handle in page.locator(selector).all()[:40]:
            try:
                text = " ".join(handle.inner_text(timeout=400).split())
            except Exception:
                continue
            low = text.lower()
            score = 0
            if wanted_title and wanted_title in low:
                score += 5
            if wanted_artist and wanted_artist in low:
                score += 3
            if query and query in low:
                score += 2
            if score > (best["score"] if best else 0):
                best = {"score": score, "handle": handle, "text": text[:180]}
        if best and best["score"] >= 5:
            best["handle"].click(timeout=8000)
            return best["text"]
        page.locator(selector).first.click(timeout=8000)
        return ""

    def _fetch(self, page, payload: dict) -> dict:
        recipe = self._config().get("recipe") or {}
        if not recipe.get("search_field"):
            return {"ok": False, "error": "Record the media server search field first."}
        title = payload.get("title") or ""
        artist = payload.get("artist") or ""
        query = clean_text(payload.get("query") or f"{artist} {core_title(title)}")
        delay = float(payload.get("delay") or 1.5)
        field = page.locator(recipe["search_field"]).first
        field.click(timeout=8000)
        field.fill("")
        field.press_sequentially(query, delay=35)
        page.wait_for_timeout(int(delay * 1000))
        if recipe.get("search_button"):
            page.locator(recipe["search_button"]).first.click(timeout=8000)
        else:
            field.press("Enter")
        page.wait_for_timeout(int(max(delay, 1.2) * 1000))
        if recipe.get("results"):
            area = page.locator(recipe["results"]).first
            area.scroll_into_view_if_needed(timeout=8000)
            box = area.bounding_box()
            if box:
                page.mouse.move(box["x"] + min(box["width"] / 2, 200), box["y"] + 30)
                page.mouse.wheel(0, 700)
            page.wait_for_timeout(800)
        matched = ""
        if recipe.get("result"):
            matched = self._click_match(page, recipe["result"], title or query, artist)
            page.wait_for_timeout(int(max(delay, 1.0) * 1000))
        if recipe.get("download"):
            page.locator(recipe["download"]).first.click(timeout=8000)
            page.wait_for_timeout(800)
        for selector in recipe.get("extra") or []:
            page.locator(selector).first.click(timeout=8000)
            page.wait_for_timeout(800)
        return {"ok": True, "query": query, "matched": matched}


media_browser = MediaBrowser()
