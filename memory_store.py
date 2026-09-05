"""Persistent, local-only memory for JARVIS."""

import json
import os
import re
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class MemoryStore:
    """A small JSON-backed memory store designed for one local JARVIS user."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, memories: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(prefix="jarvis-memory-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(memories, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        except BaseException:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
            raise

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token for token in re.findall(r"[\w'-]+", text.casefold()) if len(token) > 1}

    def add(self, content: str, category: str = "note", importance: int = 2) -> dict[str, Any]:
        clean_content = " ".join(content.split()).strip()
        if not clean_content:
            raise ValueError("Memory cannot be empty.")
        if len(clean_content) > 1200:
            raise ValueError("Memory is limited to 1,200 characters.")
        now = datetime.now(UTC).isoformat()
        with self._lock:
            memories = self._load()
            existing = next((item for item in memories if item.get("content", "").casefold() == clean_content.casefold()), None)
            if existing:
                existing["updated_at"] = now
                existing["importance"] = max(existing.get("importance", 2), max(1, min(5, importance)))
                self._save(memories)
                return existing
            memory = {
                "id": uuid.uuid4().hex,
                "content": clean_content,
                "category": category[:40] or "note",
                "importance": max(1, min(5, importance)),
                "created_at": now,
                "updated_at": now,
            }
            memories.append(memory)
            self._save(memories)
            return memory

    def search(self, query: str = "", limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            memories = self._load()
        query_tokens = self._tokens(query)
        if not query_tokens:
            return sorted(memories, key=lambda item: (item.get("importance", 2), item.get("updated_at", "")), reverse=True)[:limit]

        scored: list[tuple[int, dict[str, Any]]] = []
        for memory in memories:
            haystack = f"{memory.get('content', '')} {memory.get('category', '')}"
            score = len(query_tokens & self._tokens(haystack))
            if query.casefold() in haystack.casefold():
                score += 3
            if score:
                scored.append((score * 10 + memory.get("importance", 2), memory))
        return [memory for _, memory in sorted(scored, key=lambda pair: (pair[0], pair[1].get("updated_at", "")), reverse=True)[:limit]]

    def forget(self, query: str) -> int:
        matches = {memory["id"] for memory in self.search(query, limit=100)}
        if not matches:
            return 0
        with self._lock:
            memories = self._load()
            remaining = [memory for memory in memories if memory.get("id") not in matches]
            self._save(remaining)
        return len(memories) - len(remaining)

    def add_task(self, content: str, priority: int = 2) -> dict[str, Any]:
        memory = self.add(content, category="task", importance=priority)
        with self._lock:
            memories = self._load()
            task = next(item for item in memories if item.get("id") == memory["id"])
            task["category"] = "task"
            task["status"] = "active"
            task["priority"] = max(1, min(5, priority))
            task.pop("completed_at", None)
            task["updated_at"] = datetime.now(UTC).isoformat()
            self._save(memories)
            return task

    def tasks(self, limit: int = 10, include_completed: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            memories = self._load()
        tasks = [item for item in memories if item.get("category") == "task"]
        if not include_completed:
            tasks = [item for item in tasks if item.get("status", "active") == "active"]
        return sorted(
            tasks,
            key=lambda item: (item.get("priority", item.get("importance", 2)), item.get("updated_at", "")),
            reverse=True,
        )[:limit]

    def complete_task(self, query: str) -> dict[str, Any] | None:
        query_tokens = self._tokens(query)
        if not query_tokens:
            return None
        with self._lock:
            memories = self._load()
            candidates: list[tuple[int, dict[str, Any]]] = []
            for task in memories:
                if task.get("category") != "task" or task.get("status", "active") != "active":
                    continue
                content = task.get("content", "")
                score = len(query_tokens & self._tokens(content))
                if query.casefold() in content.casefold():
                    score += 3
                if score:
                    candidates.append((score, task))
            if not candidates:
                return None
            task = max(candidates, key=lambda item: item[0])[1]
            now = datetime.now(UTC).isoformat()
            task["status"] = "completed"
            task["completed_at"] = now
            task["updated_at"] = now
            self._save(memories)
            return task

    def count(self) -> int:
        with self._lock:
            return len(self._load())
