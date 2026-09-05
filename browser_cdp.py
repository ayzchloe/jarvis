"""Phase 3: live control of the user's Chrome via the DevTools Protocol.

Attaches to an already-running Chrome (launched with --remote-debugging-port=9222)
or starts one with a dedicated Jarvis profile, then drives it *visibly* on screen:
navigate, search, type, read page text, click links by label.
Uses only the lightweight `websocket-client` package (no Playwright needed).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.browser")

try:
    from websocket import create_connection  # type: ignore
except ImportError:  # pragma: no cover
    create_connection = None

DEBUG_PORT = 9222
ENDPOINT = f"http://127.0.0.1:{DEBUG_PORT}"
PROFILE_DIR = Path(__file__).resolve().parent / "data" / "jarvis_browser_profile"
CMD_TIMEOUT = 20
_LAUNCH_LOCK = threading.Lock()
_LAUNCHED_PID: int | None = None
PROFILE_SIZE_CAP_MB = 5
_last_profile_check = 0.0


def _profile_size_mb() -> float:
    if not PROFILE_DIR.exists():
        return 0.0
    total = 0
    try:
        for f in PROFILE_DIR.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except OSError:
        pass
    return total / (1024 * 1024)


def _clean_profile_cache() -> int:
    """Delete cache, cookies, and browsing data from the Jarvis profile.
    Returns bytes freed. Keeps the profile structure intact."""
    freed = 0
    cache_dirs = [
        PROFILE_DIR / "Default" / "Cache",
        PROFILE_DIR / "Default" / "Code Cache",
        PROFILE_DIR / "Default" / "GPUCache",
        PROFILE_DIR / "Default" / "Service Worker" / "CacheStorage",
        PROFILE_DIR / "Default" / "Service Worker" / "ScriptCache",
        PROFILE_DIR / "Default" / "BudgetDatabase",
        PROFILE_DIR / "Default" / "Sessions",
        PROFILE_DIR / "Default" / "DawnCache",
        PROFILE_DIR / "Default" / "Download Service",
        PROFILE_DIR / "Default" / "File System",
        PROFILE_DIR / "Default" / "GCM Store",
        PROFILE_DIR / "Default" / "IndexedDB",
        PROFILE_DIR / "Default" / "Local Extension Settings",
        PROFILE_DIR / "Default" / "Sync Extension Settings",
        PROFILE_DIR / "ShaderCache",
    ]
    for d in cache_dirs:
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file():
                    try:
                        freed += f.stat().st_size
                        f.unlink(missing_ok=True)
                    except OSError:
                        pass
            # Remove empty dirs
            try:
                for dirpath in sorted(d.rglob("*"), reverse=True):
                    if dirpath.is_dir():
                        dirpath.rmdir()
                d.rmdir()
            except OSError:
                pass

    # Clean cookies file (just delete it, Chrome recreates)
    cookies = PROFILE_DIR / "Default" / "Cookies"
    if cookies.exists():
        try:
            freed += cookies.stat().st_size
            cookies.unlink()
        except OSError:
            pass

    # Clean browsing data JSON files
    for name in ["History", "Web Data", "Login Data", "Top Sites", "Visited Links"]:
        p = PROFILE_DIR / "Default" / name
        if p.exists():
            try:
                freed += p.stat().st_size
                p.unlink()
            except OSError:
                pass

    return freed


def check_profile_size() -> dict:
    """Check profile size and auto-clean if over cap. Returns status dict."""
    size_mb = _profile_size_mb()
    if size_mb <= PROFILE_SIZE_CAP_MB:
        return {"size_mb": round(size_mb, 2), "cap_mb": PROFILE_SIZE_CAP_MB, "cleaned": False}
    freed = _clean_profile_cache()
    new_size = _profile_size_mb()
    return {
        "size_mb": round(new_size, 2),
        "cap_mb": PROFILE_SIZE_CAP_MB,
        "cleaned": True,
        "freed_mb": round(freed / (1024 * 1024), 2),
        "original_mb": round(size_mb, 2),
    }


class BrowserUnavailableError(RuntimeError):
    pass


class ChromeUnavailableError(BrowserUnavailableError):
    pass


class ChromeCommandError(RuntimeError):
    pass


def _discover_chrome() -> str | None:
    """Find Chrome first, Edge only as last resort."""
    chrome_paths = [
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    ]
    for candidate in chrome_paths:
        if Path(candidate).is_file():
            return candidate
    chrome = shutil.which("chrome")
    if chrome:
        return chrome
    # Edge only if Chrome is truly not installed
    edge_paths = [
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in edge_paths:
        if Path(candidate).is_file():
            return candidate
    return shutil.which("msedge") or None


def _endpoint_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{ENDPOINT}/json/version", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def _kill_stray_chrome() -> None:
    """Kill any Chrome processes using our profile dir to avoid port conflicts."""
    global _LAUNCHED_PID
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.info["name"] and "chrome" in proc.info["name"].lower():
                    cmdline = " ".join(proc.info["cmdline"] or [])
                    if str(PROFILE_DIR) in cmdline and proc.info["pid"] != _LAUNCHED_PID:
                        proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass  # psutil not available, skip cleanup


def _find_chrome_with_debug_port() -> str | None:
    """Find a Chrome process already running with --remote-debugging-port=9222."""
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.info["name"] and "chrome" in proc.info["name"].lower():
                    cmdline = " ".join(proc.info["cmdline"] or [])
                    if f"--remote-debugging-port={DEBUG_PORT}" in cmdline:
                        return ENDPOINT
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass
    return None


def _find_user_chrome_profile() -> Path | None:
    """Find the user's default Chrome profile directory."""
    # Windows default Chrome profile
    candidates = [
        Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Default")),
        Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Profile 1")),
        Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Profile 2")),
    ]
    for c in candidates:
        if c.exists() and (c / "Preferences").exists():
            return c
    return None


def _launch_user_chrome_with_debug() -> subprocess.Popen | None:
    """Launch Chrome with remote debugging using the user's profile.
    Returns the process if launched, None if Chrome is already running (can't attach debug port to running instance)."""
    chrome = _discover_chrome()
    if not chrome:
        return None
    user_profile = _find_user_chrome_profile()
    if not user_profile:
        return None
    
    # Check if Chrome is already running (without debug port)
    chrome_running = False
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            if proc.info["name"] and "chrome" in proc.info["name"].lower():
                chrome_running = True
                break
    except ImportError:
        pass
    
    if chrome_running:
        # Can't attach debug port to running Chrome - user must restart
        return None
    
    # Launch with user's profile and debug port
    proc = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={user_profile.parent}",  # User Data dir, not Profile dir
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc


def ensure_browser(start_new: bool = True, prefer_user_session: bool = True) -> str:
    """Make sure a CDP-capable Chrome is reachable; returns the endpoint.
    
    Priority:
    1. Existing Chrome with --remote-debugging-port=9222 (user's logged-in session)
    2. If prefer_user_session and no debug Chrome: launch user's Chrome with debug port
    3. Fallback: Launch Jarvis profile Chrome (separate profile, no logins)
    """
    global _LAUNCHED_PID, _last_profile_check
    
    # Auto-clean profile if over 5 MB (throttled to once per 60s)
    now = time.time()
    if now - _last_profile_check > 60:
        _last_profile_check = now
        try:
            status = check_profile_size()
            if status.get("cleaned"):
                log.info(
                    f"[PROFILE] Auto-cleaned: {status['original_mb']} MB -> {status['size_mb']} MB "
                    f"(freed {status['freed_mb']} MB)"
                )
        except Exception:
            pass
    if create_connection is None:
        raise ChromeUnavailableError("websocket-client is not installed (pip install websocket-client).")
    
    # 1. Check if there's already a Chrome with debug port (could be user's or ours)
    if _endpoint_alive():
        return ENDPOINT
    
    if not start_new:
        raise ChromeUnavailableError(
            "No browser is on the debugging port. Start Chrome with "
            f"--remote-debugging-port={DEBUG_PORT} or let JARVIS launch one."
        )
    
    with _LAUNCH_LOCK:
        # Double-check after acquiring lock
        if _endpoint_alive():
            return ENDPOINT
        
        # 2. Try to launch user's Chrome with debug port (gets their logins)
        if prefer_user_session:
            proc = _launch_user_chrome_with_debug()
            if proc:
                _LAUNCHED_PID = proc.pid
                for _ in range(30):
                    if _endpoint_alive():
                        return ENDPOINT
                    time.sleep(0.5)
                # If we launched it but port didn't open, kill it and fall through
                try:
                    proc.kill()
                except Exception:
                    pass
        
        # 3. Fallback: Kill stray Jarvis Chrome and launch our own profile
        _kill_stray_chrome()
        chrome = _discover_chrome()
        if not chrome:
            raise ChromeUnavailableError("Chrome/Edge not found on this PC.")
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={DEBUG_PORT}",
                f"--user-data-dir={PROFILE_DIR}",
                "--remote-allow-origins=*",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _LAUNCHED_PID = proc.pid
        for _ in range(30):
            if _endpoint_alive():
                return ENDPOINT
            time.sleep(0.5)
        raise ChromeUnavailableError("Chrome started but the debugging port never opened.")


class LiveBrowser:
    """Minimal CDP client over the DevTools websocket for one visible page."""

    def __init__(self, timeout: int = 20) -> None:
        self.ws = None
        self.timeout = timeout
        self._lock = threading.RLock()
        self._last_reconnect = 0.0

    # -- connection ---------------------------------------------------------

    def _pick_page_url(self) -> str:
        """Select the best page target: prefer active/foreground tab, then visible non-internal pages."""
        with urllib.request.urlopen(f"{ENDPOINT}/json", timeout=3) as response:
            targets = json.loads(response.read().decode("utf-8"))
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            raise ChromeUnavailableError("No open Chrome page to control.")
        # 1. Prefer active/foreground tab
        active = [t for t in pages if t.get("active") is True or t.get("focused") is True]
        if active:
            return active[0]["webSocketDebuggerUrl"]
        # 2. Prefer visible non-internal pages (not chrome://, devtools://, extensions, about:blank)
        visible = [
            t for t in pages
            if not t.get("url", "").startswith(("chrome://", "devtools://", "chrome-extension://"))
            and t.get("url", "") != "about:blank"
        ]
        if visible:
            return visible[0]["webSocketDebuggerUrl"]
        # 3. Any non-blank page
        non_blank = [t for t in pages if t.get("url", "") != "about:blank"]
        if non_blank:
            return non_blank[0]["webSocketDebuggerUrl"]
        # 4. Fallback: first page
        return pages[0]["webSocketDebuggerUrl"]

    def _connect_ws(self, url: str) -> None:
        self.ws = create_connection(url, timeout=self.timeout)
        self.command("Page.enable")
        self.command("Runtime.enable")

    def connect(self, reconnect: bool = False) -> None:
        if self.ws and not reconnect:
            return
        ensure_browser()
        url = self._pick_page_url()
        self._connect_ws(url)

    def _ensure_connected(self) -> None:
        """Reconnect if WebSocket is dead. Throttled to once per 2 seconds."""
        now = time.time()
        if self.ws and now - self._last_reconnect < 2:
            return
        try:
            # Ping to check connection
            self.ws.send(json.dumps({"id": -1, "method": "Runtime.evaluate", "params": {"expression": "1"}}))
            self.ws.recv()
        except Exception:
            self._last_reconnect = now
            self.close()
            self.connect(reconnect=True)

    def close(self) -> None:
        try:
            if self.ws:
                self.ws.close()
        finally:
            self.ws = None

    def fresh_tab(self, url: str = "about:blank") -> None:
        """Create a brand-new page target and attach to it."""
        from urllib.parse import quote
        req = urllib.request.Request(f"{ENDPOINT}/json/new?{quote(url)}", method="PUT")
        with urllib.request.urlopen(req, timeout=5) as response:
            target = json.loads(response.read().decode("utf-8"))
        self.close()
        self._connect_ws(target["webSocketDebuggerUrl"])

    def command(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        self._ensure_connected()
        if not self.ws:
            raise ChromeUnavailableError("Browser not connected after reconnect.")
        with self._lock:
            request_id = getattr(self, "_msg_id", 0) + 1
            self._msg_id = request_id
            try:
                self.ws.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
            except Exception as e:
                self.close()
                raise ChromeCommandError(f"CDP send failed: {e}")
            deadline = time.time() + (timeout or self.timeout)
            while time.time() < deadline:
                try:
                    message = json.loads(self.ws.recv())
                except Exception as e:
                    self.close()
                    raise ChromeCommandError(f"CDP recv failed: {e}")
                if message.get("id") == request_id:
                    if "error" in message:
                        raise ChromeCommandError(str(message["error"]))
                    return message.get("result", {})
        raise ChromeCommandError(f"CDP command {method} timed out.")
# -- page actions -------------------------------------------------------

    def evaluate(self, expression: str, timeout: float | None = None) -> Any:
        result = self.command("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        }, timeout=timeout)
        value = result.get("result", {})
        if value.get("subtype") == "error":
            raise ChromeCommandError(f"JS error: {value.get('description', '')}")
        return value.get("value")

    def navigate(self, url: str) -> str:
        address = url.strip()
        if not address.startswith(("http://", "https://")):
            address = f"https://{address}"
        self.command("Page.navigate", {"url": address})
        self.wait_ready(15)
        return self.current_url()

    def current_url(self) -> str:
        return str(self.evaluate("location.href"))

    def title(self) -> str:
        return str(self.evaluate("document.title"))

    def page_text(self, limit: int = 4000) -> str:
        text = str(self.evaluate("document.body ? document.body.innerText : ''"))
        return text[:limit]

    def read_page(self, limit: int = 4000) -> dict[str, Any]:
        return {
            "url": self.current_url(),
            "title": self.title(),
            "text": self.page_text(limit),
        }

    def wait_ready(self, max_seconds: float = 15) -> None:
        deadline = time.time() + max_seconds
        while time.time() < deadline:
            try:
                state = str(self.evaluate("document.readyState"))
                if state == "complete":
                    return
            except ChromeCommandError:
                pass
            time.sleep(0.4)
        raise ChromeCommandError("Page did not finish loading in time.")

    def search_and_enter(self, query: str, box_selector: str | None = None) -> dict[str, Any]:
        selectors = [box_selector] if box_selector else [
            'input[name="search_query"]',
            'input[type="search"]',
            'input[placeholder*="earch" i]',
            'textarea[placeholder*="earch" i]',
            'input[title*="earch" i]',
        ]
        picked = None
        for selector in selectors:
            if not selector:
                continue
            found = self.evaluate(
                "(() => { const el = document.querySelector(" + json.dumps(selector) + "); "
                "return el ? el.getBoundingClientRect().width > 0 : false; })()"
            )
            if found:
                picked = selector
                break
        if not picked:
            raise ChromeCommandError("No search box found on this page.")
        self.evaluate(
            "(() => { const el = document.querySelector(" + json.dumps(picked) + "); "
            "el.focus(); el.click(); return true; })()"
        )
        time.sleep(0.3)
        self.command("Input.insertText", {"text": query[:2000]})
        self.press("Enter")
        return {"searched": query, "box": picked}

    def press(self, key: str) -> None:
        key_map = {"enter": "Enter", "escape": "Escape", "tab": "Tab", "backspace": "Backspace"}
        code = key_map.get(key.casefold(), key)
        self.command("Input.dispatchKeyEvent", {"type": "keyDown", "key": code, "code": code})
        self.command("Input.dispatchKeyEvent", {"type": "keyUp", "key": code, "code": code})

    def click_first_link_containing(self, text: str) -> bool:
        script = (
            "(() => { const needle = "
            + json.dumps(text)
            + ".toLowerCase(); const links = Array.from(document.querySelectorAll('a')); "
            "const el = links.find(x => (x.innerText || '').toLowerCase().includes(needle)); "
            "if (!el) return false; el.scrollIntoView({block:'center'}); el.click(); return true; })()"
        )
        found = bool(self.evaluate(script))
        if not found:
            raise ChromeCommandError(f"No link containing {text!r} found on the page.")
        time.sleep(1.5)
        return True

    def click_video_result(self) -> bool:
        script = (
            "(() => { const el = document.querySelector('a#video-title'); "
            "if (!el) return false; el.scrollIntoView({block:'center'}); el.click(); return true; })()"
        )
        found = bool(self.evaluate(script))
        if found:
            time.sleep(2)
        return found

    def goto_and_wait(self, url: str) -> None:
        self.navigate(url)
        self.wait_ready(15)

    def search_google_maps(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search Google Maps for a business/place query and return the real result rows.

        Returns live rows (name, rating, address, maps_link, raw details) extracted
        from the visible Maps search page — never synthesized.
        """
        from urllib.parse import quote
        # Reuse existing tab instead of creating a new one
        try:
            self.navigate("https://www.google.com/maps/search/" + quote(query))
        except Exception:
            # If current tab is dead, create a fresh one
            self.fresh_tab("https://www.google.com/maps/search/" + quote(query))
        time.sleep(1)
        # Dismiss common consent / location overlays.
        for _ in range(2):
            time.sleep(1.5)
            dismiss = (
                "(() => {"
                " const btns = Array.from(document.querySelectorAll('button'));"
                " const hit = btns.find(b => {"
                "   const t = (b.innerText || '').trim().toLowerCase();"
                "   return ['reject all','accept all','ok','got it','dismiss','not now','i agree'].includes(t)"
                "     || /reject all/.test(t) || /accept all/.test(t) || /got it/.test(t);"
                " });"
                " if (hit) { hit.click(); return true; } return false; })()"
            )
            try:
                self.evaluate(dismiss)
            except Exception:
                pass
        # Wait for the results feed (with place anchors) to actually render.
        ready = False
        anchor_found = False
        for _ in range(12):
            try:
                if self.evaluate("!!document.querySelector('a[href*=\"/maps/place/\"]')"):
                    ready = True
                    anchor_found = True
                    break
            except Exception:
                pass
            time.sleep(1)
        # Scroll the feed to trigger lazy rendering.
        for _ in range(min(8, (int(limit) // 3) + 2)):
            self.evaluate(
                "(() => { const f = document.querySelector('[role=feed]'); "
                "if (f) f.scrollBy(0, 1000); return true; })()"
            )
            time.sleep(0.7)
        if not ready:
            self.evaluate(
                "(() => { const e = document.querySelector('[role=main]') || document.body; "
                "e.scrollBy(0, 800); return true; })()"
            )
        max_rows = max(1, min(int(limit), 30))
        script = (
            "(() => {\n"
            "  const out = [];\n"
            "  const seen = new Set();\n"
            "  const anchors = Array.from(document.querySelectorAll(\"a[href*='/maps/place/']\"));\n"
            "  for (const a of anchors) {\n"
            "    const name = (a.innerText || '').replace(/\\s{2,}/g, ' ').trim();\n"
            "    if (!name || seen.has(name)) continue;\n"
            "    seen.add(name);\n"
            "    let cur = a;\n"
            "    let rating = '';\n"
            "    for (let i = 0; i < 4; i++) {\n"
            "      cur = cur.parentElement;\n"
            "      if (!cur) break;\n"
            "      const ls = (cur.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);\n"
            "      rating = ls.find(l => /^\\d+(\\.\\d+)?\\s*\\([\\d,]+\\s*\\)/.test(l)) || (ls.find(l => /stars/i.test(l)) || '');\n"
            "      if (rating) break;\n"
            "    }\n"
            "    const text = (cur && cur.innerText) || (a.parentElement ? a.parentElement.innerText : '');\n"
            "    const lines = (text || '').split('\\n').map(s => s.trim()).filter(Boolean);\n"
            "    const nonName = lines.filter(l => l && l !== name);\n"
            "    const address = nonName.find(l => !/^\\d+(\\.\\d+)?\\s*\\([\\d,]+\\s*\\)/.test(l) "
            "      && (/^\\d{1,6}[A-Za-z]?\\s/.test(l) || /\\b(street|street,|road|rd|avenue|ave|blvd|boulevard|lane|drive|way|highway|parkway|court)\\b/i.test(l))) "
            "      || nonName[1] || nonName[0] || '';\n"
            "    const href = a.getAttribute('href') || '';\n"
            "    out.push({\n"
            "      name: name,\n"
            "      rating: rating,\n"
            "      address: address,\n"
            "      maps_link: href.startsWith('http') ? href : ('https://www.google.com' + href),\n"
            "      details: nonName.slice(0, 5)\n"
            "    });\n"
            "    if (out.length >= LIMIT) break;\n"
            "  }\n"
            "  return out;\n"
            "})()"
        ).replace("LIMIT", json.dumps(max_rows))
        rows = list(self.evaluate(script) or [])
        import re as _re
        for row in rows:
            if row.get("address"):
                addr = row["address"]
                addr = _re.sub(r"\s*[\u00b7\ue000-\uf8ff\ufffd]+\s*", " ", addr)
                addr = _re.sub(r"\s+", " ", addr).strip()
                addr = _re.sub(r"^[^A-Za-z0-9#]+", "", addr)
                row["address"] = addr
        return rows