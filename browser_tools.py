#!/usr/bin/env python3
"""
Enhanced Browser Tools — Production-grade automation for agents.
Includes: scroll, wait, extract, loop, anti-bot evasion, schema-based extraction.
"""
import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from browser_cdp import LiveBrowser

log = logging.getLogger("jarvis.browser")


@dataclass
class ExtractionSchema:
    """Defines what data to extract from a page."""
    fields: Dict[str, Dict]  # field_name -> {type, selector?, attribute?, transform?}
    container: str  # CSS selector for repeating items
    pagination: Optional[Dict] = None  # {next_selector, max_pages}


class BrowserTools:
    """Production-grade browser automation for agent workflows."""

    def __init__(self, headless: bool = False, profile_dir: Optional[Path] = None):
        self.browser = LiveBrowser()
        self.headless = headless
        self.profile_dir = profile_dir
        self._page = None
        self._session_cookies = {}
        self._last_action_time = 0
        self._min_delay = 1.5  # seconds between actions
        self._max_delay = 4.0

    # ──────────────────────────────────────────────────────────────────────────
    # Core navigation & session
    # ──────────────────────────────────────────────────────────────────────────

    def connect(self, url: str = "about:blank") -> str:
        """Ensure browser is connected and navigate to URL."""
        if self._page is None:
            self.browser.connect()
        return self.navigate(url)

    def navigate(self, url: str) -> str:
        """Navigate to URL with anti-bot delay."""
        self._human_delay()
        result = self.browser.navigate(url)
        self._wait_for_load()
        return result

    def _human_delay(self):
        """Random delay to mimic human behavior."""
        elapsed = time.time() - self._last_action_time
        if elapsed < self._min_delay:
            time.sleep(random.uniform(self._min_delay - elapsed, self._max_delay - elapsed))
        self._last_action_time = time.time()

    def _wait_for_load(self, timeout: float = 15.0):
        """Wait for page to finish loading."""
        try:
            self.browser.wait_ready(timeout)
        except Exception:
            pass  # Non-fatal

    def close(self):
        """Close browser connection."""
        if self.browser:
            self.browser.close()
            self._page = None

    # ──────────────────────────────────────────────────────────────────────────
    # Interaction primitives
    # ──────────────────────────────────────────────────────────────────────────

    def click(self, selector: str, wait_after: float = 1.0) -> bool:
        """Click element by CSS selector."""
        self._human_delay()
        try:
            self.browser.command("DOM.querySelector", {"selector": selector})
            # Use CDP to click
            self.browser.command("Input.dispatchMouseEvent", {
                "type": "mousePressed", "button": "left", "clickCount": 1
            })
            self.browser.command("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "button": "left", "clickCount": 1
            })
            if wait_after:
                time.sleep(wait_after)
            return True
        except Exception as e:
            log.warning(f"Click failed on {selector}: {e}")
            return False

    def type_text(self, selector: str, text: str, clear_first: bool = True, delay: float = 0.05) -> bool:
        """Type text into input field with human-like delays."""
        self._human_delay()
        try:
            if clear_first:
                # Select all and delete
                self.browser.command("Input.dispatchKeyEvent", {
                    "type": "keyDown", "key": "Control", "modifiers": 2
                })
                self.browser.command("Input.dispatchKeyEvent", {
                    "type": "keyDown", "key": "a", "modifiers": 2
                })
                self.browser.command("Input.dispatchKeyEvent", {
                    "type": "keyUp", "key": "a", "modifiers": 2
                })
                self.browser.command("Input.dispatchKeyEvent", {
                    "type": "keyUp", "key": "Control", "modifiers": 2
                })
            for char in text:
                self.browser.command("Input.dispatchKeyEvent", {
                    "type": "char", "text": char
                })
                time.sleep(random.uniform(0.03, 0.12))
            return True
        except Exception as e:
            log.warning(f"Type failed on {selector}: {e}")
            return False

    def scroll(self, direction: str = "down", amount: int = 500) -> bool:
        """Scroll page by pixels."""
        try:
            delta = amount if direction == "down" else -amount
            self.browser.command("Input.dispatchMouseEvent", {
                "type": "mouseWheel", "deltaX": 0, "deltaY": delta
            })
            time.sleep(0.5)
            return True
        except Exception as e:
            log.warning(f"Scroll failed: {e}")
            return False

    def scroll_to_bottom(self, max_scrolls: int = 20, pause: float = 1.5) -> int:
        """Scroll to bottom of page (for infinite scroll). Returns scrolls performed."""
        scrolls = 0
        last_height = 0
        for _ in range(max_scrolls):
            self.scroll("down", 800)
            time.sleep(pause)
            scrolls += 1
            # Could check document height here via evaluate
        return scrolls

    def wait_for(self, selector: str, timeout: float = 15.0, visible: bool = True) -> bool:
        """Wait for element to appear (polling)."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                # Try to find element
                result = self.browser.command("DOM.querySelector", {"selector": selector})
                if result and result.get("nodeId"):
                    if not visible:
                        return True
                    # Check visibility via computed style
                    style = self.browser.command("CSS.getComputedStyleForNode", {
                        "nodeId": result["nodeId"]
                    })
                    if style and style.get("computedStyle", {}).get("display", "none") != "none":
                        return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def evaluate(self, script: str) -> Any:
        """Execute JavaScript in page context."""
        try:
            return self.browser.evaluate(script)
        except Exception as e:
            log.warning(f"Evaluate failed: {e}")
            return None

    # ──────────────────────────────────────────────────────────────────────────
    # Schema-based extraction
    # ──────────────────────────────────────────────────────────────────────────

    def extract(self, schema: Union[Dict, 'ExtractionSchema']) -> List[Dict]:
        """
        Extract structured data from page using schema.
        Schema can be a dict or ExtractionSchema object.
        """
        if hasattr(schema, 'fields'):
            # ExtractionSchema object
            fields = schema.fields
            container = schema.container
            pagination = schema.pagination
        else:
            fields = schema.get("fields", {})
            container = schema.get("container", "")
            pagination = schema.get("pagination")

        results = []

        if container:
            # Extract repeating items
            items = self._extract_items(container, fields, pagination)
            results.extend(items)
        else:
            # Single item extraction
            item = self._extract_single(fields)
            if item:
                results.append(item)

        return results

    def _extract_items(self, container: str, fields: Dict, pagination: Optional[Dict]) -> List[Dict]:
        items = []
        max_pages = pagination.get("max_pages", 1) if pagination else 1
        next_selector = pagination.get("next_selector") if pagination else None

        for page in range(max_pages):
            # Get all container elements
            script = f"""
                const items = document.querySelectorAll('{container}');
                return Array.from(items).map((el, i) => {{
                    const data = {{}};
                    {self._generate_field_extraction_js(fields)}
                    return data;
                }});
            """
            page_items = self.evaluate(script)
            if page_items:
                items.extend(page_items)

            # Pagination
            if pagination and page < max_pages - 1:
                if not self.click(pagination["next_selector"], wait_after=2.0):
                    break
                time.sleep(2)

        return items

    def _generate_field_extraction_js(self, fields: Dict) -> str:
        js_parts = []
        for name, spec in fields.items():
            selector = spec.get("selector", "")
            attr = spec.get("attribute", "textContent")
            transform = spec.get("transform")

            if selector:
                js_parts.append(f"""
                    const el_{name} = el.querySelector('{selector}');
                    data['{name}'] = el_{name} ? el_{name}.{attr} : null;
                """)
            else:
                js_parts.append(f"data['{name}'] = null;")

            if transform:
                js_parts.append(f"data['{name}'] = {transform}(data['{name}']);")

        return "\n".join(js_parts)

    def _extract_single(self, fields: Dict) -> Optional[Dict]:
        script = f"""
            const data = {{}};
            {self._generate_field_extraction_js(fields)}
            return data;
        """
        return self.evaluate(script)

    # ──────────────────────────────────────────────────────────────────────────
    # Loop / pagination helper
    # ──────────────────────────────────────────────────────────────────────────

    def loop(self, condition: Dict, steps: List[Dict], max_iterations: int = 50) -> List[Dict]:
        """
        Execute a loop of steps until condition is met or max iterations reached.
        condition: {type: "selector_exists"|"text_contains"|"max_items", selector/text/max}
        steps: list of {action, params}
        """
        results = []
        for i in range(max_iterations):
            # Check condition
            if self._check_condition(condition):
                break

            # Execute steps
            step_results = {}
            for step in steps:
                action = step.get("action")
                params = step.get("params", {})
                try:
                    if action == "scroll":
                        self.scroll(params.get("direction", "down"), params.get("amount", 500))
                    elif action == "click":
                        self.click(params.get("selector"))
                    elif action == "wait":
                        time.sleep(params.get("seconds", 1))
                    elif action == "extract":
                        step_results[step.get("name", "extract")] = self.extract(params.get("schema"))
                    elif action == "wait_for":
                        self.wait_for(params.get("selector"))
                    step_results[f"step_{action}"] = "ok"
                except Exception as e:
                    step_results[f"step_{action}"] = f"error: {e}"

            results.append({"iteration": i, "results": step_results})

            # Random delay between iterations
            time.sleep(random.uniform(1.0, 2.5))

        return results

    def _check_condition(self, condition: Dict) -> bool:
        ctype = condition.get("type")
        if ctype == "selector_exists":
            return bool(self.evaluate(f"document.querySelector('{condition.get('selector')}')"))
        elif ctype == "text_contains":
            return condition.get("text", "").lower() in (self.evaluate("document.body.innerText") or "").lower()
        elif ctype == "max_items":
            # Would need context of how many items collected
            return False
        return False

    # ──────────────────────────────────────────────────────────────────────────
    # Screenshot / debug
    # ──────────────────────────────────────────────────────────────────────────

    def screenshot(self, path: str, full_page: bool = False) -> str:
        """Take screenshot."""
        try:
            if full_page:
                # Scroll and stitch
                pass
            # Simple viewport screenshot via CDP
            result = self.browser.command("Page.captureScreenshot", {"format": "png", "fromSurface": True})
            import base64
            with open(path, "wb") as f:
                f.write(base64.b64decode(result["data"]))
            return path
        except Exception as e:
            log.warning(f"Screenshot failed: {e}")
            return ""

    # ──────────────────────────────────────────────────────────────────────────
    # Anti-bot utilities
    # ──────────────────────────────────────────────────────────────────────────

    def random_mouse_move(self):
        """Move mouse to random position."""
        x = random.randint(100, 800)
        y = random.randint(100, 600)
        self.browser.command("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y
        })

    def simulate_reading(self, min_seconds: float = 2.0, max_seconds: float = 8.0):
        """Simulate human reading time."""
        time.sleep(random.uniform(min_seconds, max_seconds))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# ──────────────────────────────────────────────────────────────────────────────
# High-level agent workflows
# ──────────────────────────────────────────────────────────────────────────────

async def linkedin_search_leads(browser: BrowserTools, query: str, max_results: int = 50) -> List[Dict]:
    """Search LinkedIn for leads and extract profiles."""
    browser.navigate(f"https://www.linkedin.com/search/results/people/?keywords={query}")
    time.sleep(3)

    schema = {
        "container": "li.reusable-search__result-container",
        "fields": {
            "name": {"selector": ".entity-result__title-text a", "attribute": "textContent"},
            "profile_url": {"selector": ".entity-result__title-text a", "attribute": "href"},
            "headline": {"selector": ".entity-result__primary-subtitle", "attribute": "textContent"},
            "location": {"selector": ".entity-result__secondary-subtitle", "attribute": "textContent"},
        },
        "pagination": {"next_selector": "button[aria-label='Next']", "max_pages": 3}
    }
    return browser.extract(schema)


async def instagram_search_leads(browser: BrowserTools, hashtag: str, max_posts: int = 50) -> List[Dict]:
    """Search Instagram by hashtag and extract profile data."""
    browser.navigate(f"https://www.instagram.com/explore/tags/{hashtag}/")
    time.sleep(3)
    # Implementation would scroll, click posts, extract profile data
    return []


# ──────────────────────────────────────────────────────────────────────────────
# Singleton
# ──────────────────────────────────────────────────────────────────────────────

_browser: Optional[BrowserTools] = None


def get_browser(headless: bool = False) -> BrowserTools:
    global _browser
    if _browser is None:
        _browser = BrowserTools(headless=headless)
    return _browser


if __name__ == "__main__":
    # Quick test
    logging.basicConfig(level=logging.INFO)
    with BrowserTools() as b:
        b.navigate("https://example.com")
        print(b.extract({"fields": {"title": {"selector": "h1", "attribute": "textContent"}}}))