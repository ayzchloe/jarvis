"""JARVIS skills: file-defined voice routines, capability prompts, and scripts.

Every JSON file in the skills/ directory (project root / skills/*.json) is loaded
at startup. A skill may combine three mechanisms:

  steps   -> ordered list of tool calls (voice routine), executed back-to-back
  prompt  -> extra system-prompt guidance when the skill is triggered (capability)
  script  -> a Python file run with {"text", "session"} on stdin; JSON stdout with
             "reply" is treated as the spoken answer

Schema:

{
  "name": "good_morning",
  "description": "One line shown to the model and dashboard.",
  "enabled": true,
  "triggers": ["good morning", "start my morning"],
  "prompt": "Optional. Injected into the system prompt when triggered.",
  "steps": [{"tool": "system_info", "values": {}}],
  "script": "skills/scripts/screenshot_log.py"
}

At least one of steps / prompt / script should be present, and triggers should be
short spoken phrases. The dialog matches any trigger as a substring of the
utterance, so keep them distinctive.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any


class SkillStore:
    """Disk-loaded skill registry for one skills/ directory."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._lock = threading.RLock()
        self._skills: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            skills: dict[str, dict[str, Any]] = {}
            if self.directory.is_dir():
                for path in sorted(self.directory.glob("*.json")):
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if not isinstance(data, dict) or not data.get("name"):
                        continue
                    name = str(data["name"]).strip()
                    data.setdefault("description", "")
                    data.setdefault("enabled", True)
                    data.setdefault("triggers", [])
                    data.setdefault("prompt", "")
                    data.setdefault("steps", [])
                    data.setdefault("script", "")
                    data.setdefault("persist", False)
                    skills[name] = data
            self._skills = skills

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(skill) for skill in self._skills.values()]

    def get(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            skill = self._skills.get(name)
            return dict(skill) if skill else None

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any] | None:
        with self._lock:
            skill = self._skills.get(name)
            if not skill:
                return None
            skill["enabled"] = bool(enabled)
            try:
                path = self.directory / f"{name}.json"
                path.write_text(json.dumps(skill, indent=2, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
            return dict(skill)

    def by_trigger(self, text: str) -> dict[str, Any] | None:
        if not text or not text.strip():
            return None
        normalized = re.sub(r"\s+", " ", text.casefold()).strip()
        explicit = re.match(r"^(?:run|use|activate|start) (?:the )?(?:skill )?(.+)$", normalized)
        if explicit:
            candidate = explicit.group(1).strip().replace(" ", "_")
            with self._lock:
                for key, skill in self._skills.items():
                    if candidate == key or candidate in key:
                        if skill.get("enabled", True):
                            return dict(skill)
        best: dict[str, Any] | None = None
        best_len = 0
        with self._lock:
            for skill in self._skills.values():
                if not skill.get("enabled", True):
                    continue
                for trigger in skill.get("triggers", []):
                    trig = str(trigger).strip().casefold()
                    if trig and trig in normalized and len(trig) > best_len:
                        best = skill
                        best_len = len(trig)
        return dict(best) if best else None