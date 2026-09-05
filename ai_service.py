"""
JARVIS AI Service - Core orchestration layer with agent integration.

Owns the LLM tool-calling loop (Groq / Gemini), exposes the full tool surface
(Google Workspace, desktop, browser, memory, goals, skills, agents) to the
model, routes long answers to the chat console, and persists model choices.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("jarvis.ai")

os.environ.setdefault("HTTP_PROXY", os.getenv("HTTP_PROXY", ""))
os.environ.setdefault("HTTPS_PROXY", os.getenv("HTTPS_PROXY", ""))

# ---------------------------------------------------------------------------
# Model presets
# ---------------------------------------------------------------------------

MODEL_PRESETS = {
    "groq": [
        ("openai/gpt-oss-20b", "GPT-OSS 20B"),
        ("openai/gpt-oss-120b", "GPT-OSS 120B"),
    ],
    "gemini": [
        ("gemini-2.5-flash", "Gemini 2.5 Flash"),
        ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
        ("gemini-3.5-flash", "Gemini 3.5 Flash"),
        ("gemini-3.7-flash", "Gemini 3.7 Flash"),
    ],
}

MODEL_CONFIG_FILE = DATA_DIR / "model_config.json"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AssistantUnavailableError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# JarvisService
# ---------------------------------------------------------------------------

class JarvisService:
    """Central JARVIS service orchestrating memory, goals, skills, agents, and tools."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        from memory_store import MemoryStore
        self.memory = MemoryStore(self.data_dir / "jarvis_memory.json")
        self.goals_path = self.data_dir / "jarvis_goals.json"

        from skills_store import SkillStore
        self.skills = SkillStore(BASE_DIR / "skills")

        self._provider: str = "gemini"
        self._model: str = "gemini-3.5-flash-lite"
        self._load_model_config()

        self._agent_hub = None
        self._agent_hub_tried = False
        self._browser_tools = None

        self._sessions: dict[str, list[dict]] = {}

    # ---- model config persistence ----------------------------------------

    @property
    def _model_config_path(self) -> Path:
        return MODEL_CONFIG_FILE

    def _load_model_config(self) -> None:
        data = {}
        try:
            if self._model_config_path.exists():
                data = json.loads(self._model_config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        self._provider = data.get("provider", os.getenv("AI_PROVIDER", "gemini"))
        self._model = data.get("model", os.getenv("AI_MODEL", "gemini-3.5-flash-lite"))
        log.info(f"Loaded model config: {self._provider}/{self._model}")

    def _save_model_config(self) -> None:
        try:
            self._model_config_path.write_text(
                json.dumps({"provider": self._provider, "model": self._model}, indent=2),
                encoding="utf-8",
            )
        except Exception as error:
            log.warning(f"Could not save model config: {error}")

    def get_provider(self) -> str:
        return self._provider

    def get_model(self) -> str:
        return self._model

    def set_model(self, provider: str, model: str) -> None:
        valid = [p for p, _ in MODEL_PRESETS.get(provider, [])]
        if valid and model not in valid:
            raise ValueError(f"Unknown provider '{provider}' or model '{model}'. Available: {valid}")
        self._provider = provider
        self._model = model
        self._save_model_config()

    def list_models(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for provider, models in MODEL_PRESETS.items():
            out[provider] = [{"id": m[0], "name": m[1]} for m in models]
        return out

    # ---- agent hub --------------------------------------------------------

    def _init_agent_hub(self) -> None:
        if self._agent_hub_tried:
            return
        self._agent_hub_tried = True
        try:
            from agent_hub import get_agent_hub
            self._agent_hub = get_agent_hub(jarvis=self, browser=self._cdp_browser)
            log.info(f"AgentHub ready with {len(self._agent_hub.agents)} agents")
        except Exception as error:
            log.warning(f"AgentHub initialization failed: {error}")

    @property
    def agent_hub(self):
        self._init_agent_hub()
        return self._agent_hub

    @property
    def browser_tools(self):
        if self._browser_tools is None:
            try:
                from browser_tools import get_browser
                self._browser_tools = get_browser()
            except Exception:
                pass
        return self._browser_tools

    @property
    def _cdp_browser(self):
        try:
            from browser_cdp import LiveBrowser, ensure_browser
            return LiveBrowser()
        except Exception:
            return None

    # ---- public data accessors -------------------------------------------

    def health(self) -> dict[str, Any]:
        from auth import google_workspace
        google = google_workspace.status()
        connectors: list[dict[str, Any]] = []
        try:
            from connector_oauth import all_status
            connectors = all_status()
        except Exception:
            pass
        return {
            "status": "ok",
            "google": {
                "name": "Google Workspace",
                "summary": "Gmail, Calendar, Drive, Docs, Sheets, Tasks, and Contacts",
                "status": (
                    "connected" if google.get("connected")
                    else "configured" if google.get("configured")
                    else "ready_to_authorize" if google.get("client_id_set")
                    else "credentials_required"
                ),
                "connected": google.get("connected", False),
            },
            "connectors": connectors,
        }

    def system_info(self) -> dict[str, Any]:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return {
            "cpu_percent": cpu,
            "memory_percent": mem.percent,
            "memory_used_gb": round(mem.used / (1024**3), 1),
            "memory_total_gb": round(mem.total / (1024**3), 1),
            "disk_percent": disk.percent,
            "disk_used_gb": round(disk.used / (1024**3), 1),
            "disk_total_gb": round(disk.total / (1024**3), 1),
            "provider": self._provider,
            "model": self._model,
        }

    def google_status(self) -> dict[str, Any]:
        from auth import google_workspace
        return google_workspace.status()

    def connectors_status(self) -> dict[str, Any]:
        all_status: list[dict[str, Any]] = []
        try:
            from connector_oauth import all_status as _all_status
            all_status = _all_status()
        except Exception:
            pass
        return {"connectors": all_status}

    def memories(self, query: str = "", limit: int = 10) -> dict[str, Any]:
        items = self.memory.search(query, limit)
        return {"count": len(items), "memories": items}

    def tasks(self, limit: int = 10) -> dict[str, Any]:
        items = self.memory.tasks(limit)
        return {"count": len(items), "tasks": items}

    # ---- tool schema builder ---------------------------------------------

    def _tool(self, name: str, description: str, properties: dict, required: list | None = None) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required or [],
                },
            },
        }

    def tools(self) -> list[dict]:
        tools: list[dict] = []
        tools.append(self._tool(
            "get_stats",
            "Return current CPU, memory, disk usage, provider and model.",
            {},
        ))
        tools.append(self._tool(
            "google_workspace_status",
            "Return Google Workspace connection status (Gmail, Calendar, Drive, Docs, Sheets, Tasks, Contacts).",
            {},
        ))
        tools.append(self._tool(
            "remember_memory",
            "Store a fact, preference, or note in long-term memory.",
            {"content": {"type": "string", "description": "What to remember"}},
            ["content"],
        ))
        tools.append(self._tool(
            "recall_memories",
            "Search stored memories by key phrase.",
            {
                "query": {"type": "string", "description": "Search phrase"},
                "limit": {"type": "integer", "description": "Max results (default 5)"},
            },
        ))
        tools.append(self._tool(
            "forget_memory",
            "Delete memories matching a query.",
            {"query": {"type": "string", "description": "What to forget"}},
            ["query"],
        ))
        tools.append(self._tool(
            "add_task",
            "Add a task to the local task list.",
            {
                "content": {"type": "string", "description": "Task description"},
                "priority": {"type": "integer", "description": "1 (high) to 5 (low)"},
            },
            ["content"],
        ))
        tools.append(self._tool(
            "complete_task",
            "Mark a task complete by matching its text.",
            {"query": {"type": "string", "description": "Task text to match"}},
            ["query"],
        ))
        tools.append(self._tool(
            "list_tasks",
            "List active local tasks.",
            {},
        ))
        tools.append(self._tool(
            "control_pc",
            "Control this PC: focus/minimize/close windows, mouse, keyboard, clipboard, files, brightness, network, notifications, settings, winget apps.",
            {"action": {"type": "string", "description": "PC action name"}},
            ["action"],
        ))
        tools.append(self._tool(
            "get_volume",
            "Get current speaker volume (0-100).",
            {},
        ))
        tools.append(self._tool(
            "set_volume",
            "Set speaker volume.",
            {"volume": {"type": "integer", "description": "Volume level 0-100"}},
            ["volume"],
        ))
        tools.append(self._tool(
            "volume_step",
            "Raise or lower volume by a delta.",
            {"delta": {"type": "integer", "description": "Positive to raise, negative to lower"}},
        ))
        tools.append(self._tool(
            "set_brightness",
            "Set display brightness (0-100).",
            {"level": {"type": "integer", "description": "Brightness 0-100"}},
            ["level"],
        ))
        tools.append(self._tool(
            "get_brightness",
            "Get current display brightness (0-100).",
            {},
        ))
        tools.append(self._tool(
            "media_control",
            "Control media playback on this PC.",
            {"action": {"type": "string", "description": "play_pause, next, previous"}},
            ["action"],
        ))
        tools.append(self._tool(
            "quick_pc_action",
            "Common quick actions: open_tab, close_tab, open_chat, close_chat.",
            {"action": {"type": "string", "description": "Quick action name"}},
            ["action"],
        ))

        # ---- Gmail tools ----
        tools.append(self._tool(
            "gmail_scan_emails",
            "Scan Gmail for emails matching a query; returns subjects/snippets/full bodies.",
            {
                "query": {"type": "string", "description": "Gmail search query (e.g. 'is:unread', 'from:alice@example.com')"},
                "max_results": {"type": "integer", "description": "Max emails to scan (default 10)"},
            },
        ))
        tools.append(self._tool(
            "gmail_read_email",
            "Read the full body of one Gmail message by ID.",
            {"message_id": {"type": "string", "description": "Gmail message ID"}},
            ["message_id"],
        ))
        tools.append(self._tool(
            "gmail_send_email",
            "Send an email via Gmail (requires user approval).",
            {
                "to": {"type": "string", "description": "Recipient email"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body"},
            },
            ["to", "subject", "body"],
        ))
        tools.append(self._tool(
            "gmail_draft_email",
            "Save a draft email in Gmail.",
            {
                "to": {"type": "string", "description": "Recipient"},
                "subject": {"type": "string", "description": "Subject"},
                "body": {"type": "string", "description": "Body"},
            },
            ["to", "subject", "body"],
        ))
        tools.append(self._tool(
            "gmail_trash_email",
            "Move an email to Gmail trash (requires approval).",
            {"message_id": {"type": "string", "description": "Message ID"}},
            ["message_id"],
        ))
        tools.append(self._tool(
            "gmail_mark_read",
            "Mark an email read or unread.",
            {
                "message_id": {"type": "string", "description": "Message ID"},
                "read": {"type": "boolean", "description": "True to mark read, false for unread"},
            },
            ["message_id"],
        ))

        # ---- Calendar tools ----
        tools.append(self._tool(
            "calendar_list_events",
            "List upcoming calendar events.",
            {"days_ahead": {"type": "integer", "description": "How many days ahead (default 7)"}},
        ))
        tools.append(self._tool(
            "calendar_events_today",
            "List today's calendar events.",
            {},
        ))
        tools.append(self._tool(
            "calendar_add_event",
            "Add a calendar event. start_time like '2026-08-30T14:00' or 'tomorrow 9am'.",
            {
                "summary": {"type": "string", "description": "Event title"},
                "start_time": {"type": "string", "description": "Start time"},
                "end_time": {"type": "string", "description": "End time (optional)"},
                "description": {"type": "string", "description": "Description"},
                "location": {"type": "string", "description": "Location"},
            },
            ["summary", "start_time"],
        ))
        tools.append(self._tool(
            "calendar_quick_add",
            "Add an event using Google natural language, e.g. 'Lunch with Sam Friday 1pm'.",
            {"text": {"type": "string", "description": "Natural language event description"}},
            ["text"],
        ))
        tools.append(self._tool(
            "calendar_update_event",
            "Update fields of an existing calendar event.",
            {
                "event_id": {"type": "string", "description": "Event ID"},
                "summary": {"type": "string", "description": "New title"},
                "start_time": {"type": "string", "description": "New start time"},
                "end_time": {"type": "string", "description": "New end time"},
            },
            ["event_id"],
        ))
        tools.append(self._tool(
            "calendar_delete_event",
            "Delete a calendar event (requires approval).",
            {"event_id": {"type": "string", "description": "Event ID"}},
            ["event_id"],
        ))

        # ---- Drive tools ----
        tools.append(self._tool(
            "drive_list_files",
            "List or search files in Google Drive.",
            {
                "query": {"type": "string", "description": "Search term"},
                "max_results": {"type": "integer", "description": "Max results (default 15)"},
            },
        ))
        tools.append(self._tool(
            "drive_upload_file",
            "Upload a local file to Google Drive.",
            {
                "local_path": {"type": "string", "description": "Local file path"},
                "parent_folder": {"type": "string", "description": "Drive folder name"},
            },
            ["local_path"],
        ))
        tools.append(self._tool(
            "drive_download_file",
            "Download a Drive file to this PC by ID.",
            {
                "file_id": {"type": "string", "description": "Drive file ID"},
                "dest_dir": {"type": "string", "description": "Local destination directory"},
            },
            ["file_id"],
        ))
        tools.append(self._tool(
            "drive_create_folder",
            "Create a folder in Google Drive.",
            {"name": {"type": "string", "description": "Folder name"}},
            ["name"],
        ))
        tools.append(self._tool(
            "drive_share_file",
            "Share a Drive file with anyone via link.",
            {"file_id": {"type": "string", "description": "File ID"}},
            ["file_id"],
        ))
        tools.append(self._tool(
            "drive_delete_file",
            "Trash a Drive file (requires approval).",
            {"file_id": {"type": "string", "description": "File ID"}},
            ["file_id"],
        ))
        tools.append(self._tool(
            "drive_bulk_delete",
            "Trash several Drive files by name (requires approval).",
            {"names": {"type": "array", "items": {"type": "string"}, "description": "File names to trash"}},
            ["names"],
        ))

        # ---- Docs tools ----
        tools.append(self._tool(
            "docs_create_document",
            "Create a new Google Doc.",
            {
                "title": {"type": "string", "description": "Document title"},
                "text": {"type": "string", "description": "Initial text content"},
            },
            ["title"],
        ))
        tools.append(self._tool(
            "docs_read_document",
            "Read text content of a Google Doc by ID.",
            {"document_id": {"type": "string", "description": "Doc ID"}},
            ["document_id"],
        ))
        tools.append(self._tool(
            "docs_append_text",
            "Append text to a Google Doc.",
            {
                "document_id": {"type": "string", "description": "Doc ID"},
                "text": {"type": "string", "description": "Text to append"},
            },
            ["document_id", "text"],
        ))
        tools.append(self._tool(
            "docs_replace_text",
            "Find and replace text in a Google Doc.",
            {
                "document_id": {"type": "string", "description": "Doc ID"},
                "search_text": {"type": "string", "description": "Text to find"},
                "replace_text": {"type": "string", "description": "Replacement text"},
            },
            ["document_id", "search_text", "replace_text"],
        ))

        # ---- Sheets tools ----
        tools.append(self._tool(
            "sheets_create_spreadsheet",
            "Create a new Google Spreadsheet.",
            {"title": {"type": "string", "description": "Spreadsheet title"}},
            ["title"],
        ))
        tools.append(self._tool(
            "sheets_read_range",
            "Read a range of a Google Sheet (A1).",
            {
                "spreadsheet_id": {"type": "string", "description": "Spreadsheet ID"},
                "range_a1": {"type": "string", "description": "A1 range notation"},
            },
            ["spreadsheet_id", "range_a1"],
        ))
        tools.append(self._tool(
            "sheets_write_range",
            "Write rows to a Google Sheet (A1).",
            {
                "spreadsheet_id": {"type": "string"},
                "range_a1": {"type": "string"},
                "values": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
            },
            ["spreadsheet_id", "range_a1", "values"],
        ))
        tools.append(self._tool(
            "sheets_append_rows",
            "Append rows to a Google Sheet.",
            {
                "spreadsheet_id": {"type": "string"},
                "values": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
            },
            ["spreadsheet_id", "values"],
        ))

        # ---- Google Tasks tools ----
        tools.append(self._tool("gtasks_list_lists", "List Google Tasks lists.", {}))
        tools.append(self._tool(
            "gtasks_list_tasks",
            "List tasks in a Google Tasks list.",
            {
                "list_id": {"type": "string", "description": "Task list ID"},
                "show_completed": {"type": "boolean", "description": "Include completed tasks"},
            },
        ))
        tools.append(self._tool(
            "gtasks_add_task",
            "Add a Google Tasks task with optional due date.",
            {
                "title": {"type": "string", "description": "Task title"},
                "due": {"type": "string", "description": "Due date (ISO)"},
                "notes": {"type": "string", "description": "Task notes"},
            },
            ["title"],
        ))
        tools.append(self._tool(
            "gtasks_delete_task",
            "Delete a Google Tasks task (requires approval).",
            {
                "task_id": {"type": "string", "description": "Task ID"},
                "list_id": {"type": "string", "description": "List ID"},
            },
            ["task_id"],
        ))

        # ---- Contacts tools ----
        tools.append(self._tool(
            "contacts_search",
            "Search Google Contacts by name, email, or phone.",
            {"query": {"type": "string", "description": "Search query"}},
            ["query"],
        ))

        # ---- Browser tools ----
        tools.append(self._tool(
            "browser_navigate",
            "Navigate to a URL in the CURRENT Chrome tab (reuses existing tab, does not create new ones).",
            {"url": {"type": "string", "description": "URL to open"}},
            ["url"],
        ))
        tools.append(self._tool(
            "browser_read",
            "Read the current page text from the controlled browser.",
            {},
        ))
        tools.append(self._tool(
            "browser_click",
            "Click the first link on the page whose text contains the given string.",
            {"text": {"type": "string", "description": "Link text to find and click"}},
            ["text"],
        ))
        tools.append(self._tool(
            "browser_search",
            "Search the web for a query in the controlled browser.",
            {"query": {"type": "string", "description": "Search query"}},
            ["query"],
        ))
        tools.append(self._tool(
            "youtube_play",
            "Open YouTube search results for a query and start the top video.",
            {"query": {"type": "string", "description": "Video search query"}},
            ["query"],
        ))
        tools.append(self._tool(
            "google_maps_search",
            "Search Google Maps for a business/place query and return the REAL result rows extracted from the live page (name, rating, address, maps link). Use this for local business leads. REQUIRED: the 'query' parameter must be non-empty - e.g. query=\"hair salon in Lekki\".",
            {
                "query": {"type": "string", "description": "Business or place search query"},
                "limit": {"type": "integer", "description": "Max results (default 10)"},
            },
            ["query"],
        ))
        tools.append(self._tool(
            "restart_chrome_with_debug",
            "RESTART Chrome with --remote-debugging-port=9222 using YOUR profile (so JARVIS can access your logged-in accounts: Gmail, YouTube, etc.). WARNING: This closes ALL Chrome windows. Use when you want JARVIS to work in your logged-in session.",
            {},
        ))
        tools.append(self._tool(
            "download_url",
            "Download a file from a URL to disk.",
            {"url": {"type": "string", "description": "URL to download"}},
            ["url"],
        ))
        tools.append(self._tool(
            "run_downloaded_installer",
            "Run a downloaded installer/app by path (requires approval).",
            {"path": {"type": "string", "description": "File path to run"}},
            ["path"],
        ))
        tools.append(self._tool(
            "write_code",
            "Write a code or config file to disk, optionally overwriting.",
            {
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "File content"},
                "language": {"type": "string", "description": "Language hint"},
                "overwrite": {"type": "boolean", "description": "Overwrite existing"},
            },
            ["path", "content"],
        ))

        # ---- Goal tools ----
        tools.append(self._tool(
            "begin_task",
            "Start a multi-step goal with an ordered step list.",
            {
                "description": {"type": "string", "description": "Goal description"},
                "steps": {"type": "array", "items": {"type": "string"}, "description": "Ordered steps"},
            },
            ["description"],
        ))
        tools.append(self._tool("goal_status", "Report the current active goal and steps.", {}))
        tools.append(self._tool(
            "goal_step",
            "Log a note on the current goal step.",
            {"note": {"type": "string", "description": "Progress note"}},
            ["note"],
        ))
        tools.append(self._tool(
            "finish_goal",
            "Mark the active goal complete with a summary.",
            {"summary": {"type": "string", "description": "Completion summary"}},
        ))
        tools.append(self._tool(
            "cancel_goal",
            "Cancel the active goal.",
            {"reason": {"type": "string", "description": "Cancellation reason"}},
        ))

        # ---- Skills tools ----
        tools.append(self._tool("list_skills", "List all available skills.", {}))
        tools.append(self._tool(
            "run_skill",
            "Run a skill by name, optionally with input text.",
            {
                "name": {"type": "string", "description": "Skill name"},
                "text": {"type": "string", "description": "Input text"},
            },
            ["name"],
        ))
        tools.append(self._tool(
            "create_skill",
            "Compose a new skill from a natural-language description.",
            {"description": {"type": "string", "description": "What the skill should do"}},
            ["description"],
        ))

        # ---- Agent tools ----
        tools.append(self._tool("list_agents", "List available specialized agents.", {}))
        tools.append(self._tool(
            "spawn_agent",
            "Dispatch a task to a specialized agent by ID.",
            {
                "agent_id": {"type": "string", "description": "Agent ID (e.g. lead_hunter)"},
                "task": {"type": "string", "description": "Task to execute"},
                "autonomy": {"type": "string", "description": "low/medium/high"},
            },
            ["agent_id", "task"],
        ))
        tools.append(self._tool(
            "agent_status",
            "Get run history and status for an agent.",
            {"agent_id": {"type": "string", "description": "Agent ID"}},
            ["agent_id"],
        ))

        # ---- Connector tools ----
        tools.append(self._tool(
            "connectors_status",
            "Report configuration state for external connectors (WhatsApp, LinkedIn, Instagram, Upwork, Slack, Notion).",
            {},
        ))
        tools.append(self._tool(
            "store_secret",
            "Securely store an API key or token for a connector (encrypted on disk, never shown to the model afterward).",
            {
                "service": {"type": "string", "description": "Connector name"},
                "secret": {"type": "string", "description": "API key or token"},
            },
            ["service", "secret"],
        ))
        tools.append(self._tool(
            "get_secret",
            "Read an encrypted connector secret at call time (only in memory, never logged).",
            {"service": {"type": "string", "description": "Connector name"}},
            ["service"],
        ))
        tools.append(self._tool(
            "test_connector",
            "Verify a connector's stored credentials with a live read call.",
            {"provider": {"type": "string", "description": "Connector provider"}},
            ["provider"],
        ))
        tools.append(self._tool(
            "slack_list_channels",
            "List Slack channel names (requires Slack connected).",
            {},
        ))
        tools.append(self._tool(
            "slack_send_message",
            "Send a message to a Slack channel (requires Slack connected).",
            {
                "channel": {"type": "string", "description": "Channel name or ID"},
                "text": {"type": "string", "description": "Message text"},
            },
            ["channel", "text"],
        ))
        tools.append(self._tool(
            "linkedin_post_update",
            "Share a text update on LinkedIn (requires LinkedIn connected).",
            {"text": {"type": "string", "description": "Post text"}},
            ["text"],
        ))
        tools.append(self._tool(
            "whatsapp_send_message",
            "Send a WhatsApp message via the Cloud API (requires WhatsApp connected).",
            {
                "to": {"type": "string", "description": "Phone number or contact"},
                "message": {"type": "string", "description": "Message text"},
            },
            ["to", "message"],
        ))
        tools.append(self._tool(
            "notion_query",
            "Query a Notion database (requires Notion connected).",
            {
                "database_id": {"type": "string", "description": "Database ID"},
                "filter": {"type": "object", "description": "Optional filter"},
            },
            ["database_id"],
        ))
        tools.append(self._tool(
            "notion_create_page",
            "Create a page in a Notion database (requires Notion connected).",
            {
                "database_id": {"type": "string", "description": "Database ID"},
                "title": {"type": "string", "description": "Page title"},
                "content": {"type": "string", "description": "Page content"},
            },
            ["database_id", "title"],
        ))

        return tools

    def protected_tools(self) -> set:
        return {
            "gmail_send_email": lambda v: bool(v.get("to") and v.get("subject")),
            "gmail_trash_email": lambda v: bool(v.get("message_id")),
            "calendar_delete_event": lambda v: bool(v.get("event_id")),
            "drive_delete_file": lambda v: bool(v.get("file_id")),
            "drive_bulk_delete": lambda v: bool(v.get("names")),
            "gtasks_delete_task": lambda v: bool(v.get("task_id")),
            "run_downloaded_installer": lambda v: bool(v.get("path")),
        }

    # ---- Drive bulk delete ------------------------------------------------

    def _drive_bulk_delete(self, names: list[str]) -> dict[str, Any]:
        from auth import google_workspace as gw
        results = []
        for name in names:
            try:
                item_id = gw._find_drive_item(name)
                if item_id:
                    gw.drive_delete_file(item_id)
                    results.append({"name": name, "status": "trashed"})
                else:
                    results.append({"name": name, "status": "not_found"})
            except Exception as error:
                results.append({"name": name, "status": "error", "error": str(error)})
        trashed = sum(1 for r in results if r["status"] == "trashed")
        return {"results": results, "summary": f"Trashed {trashed} files from Drive."}

    def _run_installer(self, path: str, app_name: str = "") -> str:
        from desktop_controller import control_pc
        result = control_pc("install_app", app_name=app_name or Path(path).stem)
        return str(result)

    # ---- approval queue ---------------------------------------------------

    def queue_action(self, session: str, name: str, values: dict) -> str:
        action_id = str(uuid.uuid4())[:8]
        action = {"name": name, "values": values, "session": session}
        expires = time.time() + 300
        action["expires"] = expires
        self._sessions.setdefault(session, []).append({"id": action_id, "action": action, "status": "pending"})
        return f"Approve {action_id}"

    # ---- tool dispatcher --------------------------------------------------

    def call_tool(self, name: str, values: dict, session: str = "default", approvals: list | None = None) -> tuple:
        def g(*args, **kwargs):
            from auth import google_workspace
            return google_workspace

        def wrap_google(fn_name: str, fn, k: str, v: Any):
            try:
                result = fn(**{k: v})
                return result
            except Exception as error:
                if "not implemented" in str(error).lower():
                    return {"error": f"Google tool '{fn_name}' is not implemented."}
                return {"error": f"Google error: {error}"}

        from auth import google_workspace as _gw

        if name == "get_stats":
            return self.system_info(), None
        if name == "google_workspace_status":
            return self.google_status(), None

        if name == "remember_memory":
            mem = self.memory.add(values.get("content", ""))
            return {"result": f"Remembered: {mem['content']}", "memory": mem}, None
        if name == "recall_memories":
            items = self.memory.search(values.get("query", ""), values.get("limit", 5))
            if not items:
                return {"result": "No matching memories found."}, None
            return {"memories": items, "result": "\n".join(m.get("content", "") for m in items)}, None
        if name == "forget_memory":
            count = self.memory.forget(values.get("query", ""))
            if count:
                return {"result": f"Forgot {count} memory/memories."}, None
            return {"result": "No matching memories to forget."}, None

        if name == "add_task":
            task = self.memory.add_task(values.get("content", ""), values.get("priority", 2))
            return {"result": f"Added task: {task['content']}", "task": task}, None
        if name == "complete_task":
            task = self.memory.complete_task(values.get("query", ""))
            if task:
                return {"result": f"Completed: {task['content']}", "task": task}, None
            return {"result": "No matching active task found."}, None
        if name == "list_tasks":
            items = self.memory.tasks()
            if not items:
                return {"result": "No active tasks."}, None
            return {"tasks": items, "result": "\n".join(t.get("content", "") for t in items)}, None

        if name == "control_pc":
            from desktop_controller import control_pc
            action = values.get("action", "")
            result = control_pc(action, **{k: v for k, v in values.items() if k != "action"})
            if isinstance(result, str) and result.startswith("Unknown PC action"):
                return {"error": f"PC action '{action}' failed: {result}"}, None
            return {"result": str(result)}, None

        if name == "get_volume":
            from desktop_controller import get_volume
            vol = get_volume()
            return {"volume": vol, "percent": vol, "result": f"Volume is at {vol}%"}, None
        if name == "set_volume":
            from desktop_controller import set_volume
            set_volume(values.get("volume", 50))
            return {"result": f"Volume set to {values.get('volume', 50)}%"}, None
        if name == "volume_step":
            from desktop_controller import get_volume, set_volume
            try:
                current = get_volume()
                delta = values.get("delta", 10)
                set_volume(max(0, min(100, current + delta)))
                return {"result": f"Volume adjusted by {delta}."}, None
            except Exception as error:
                return {"error": f"Could not adjust volume: {error}"}, None
        if name == "set_brightness":
            from desktop_controller import set_brightness
            try:
                set_brightness(values.get("level", 50))
                return {"result": f"Brightness set to {values.get('level', 50)}%"}, None
            except Exception as error:
                return {"error": f"Could not set brightness: {error}"}, None
        if name == "get_brightness":
            from desktop_controller import get_brightness
            try:
                level = get_brightness()
                return {"brightness": level, "result": f"Brightness is at {level}%"}, None
            except Exception as error:
                return {"error": f"Could not get brightness: {error}"}, None
        if name == "media_control":
            from desktop_controller import media_control
            try:
                result = media_control(values.get("action", "play_pause"))
                return {"result": f"Media {values.get('action', 'play_pause')} requested."}, None
            except Exception as error:
                return {"error": f"Media control failed: {error}"}, None

        if name == "quick_pc_action":
            action = values.get("action", "")
            hotkey_map = {
                "open_tab": "ctrl+t",
                "close_tab": "ctrl+w",
                "open_chat": "ctrl+k",
                "close_chat": "escape",
            }
            if action in hotkey_map:
                from desktop_controller import keyboard_hotkey
                try:
                    keyboard_hotkey(hotkey_map[action])
                    return {"result": f"{hotkey_map[action]} shortcut."}, None
                except Exception as error:
                    return {"error": f"Shortcut failed: {error}"}, None
            return {"error": f"Unknown quick action '{action}'."}, None

        # ---- Gmail tools ----
        if name == "gmail_scan_emails":
            return self._gmail_scan(values.get("query", "is:inbox"), values.get("max_results", 10)), None
        if name == "gmail_read_email":
            from auth import google_workspace
            msg = google_workspace.read_email(values.get("message_id", ""))
            body = msg.get("body", "")
            return {
                "from": msg.get("from", ""),
                "subject": msg.get("subject", ""),
                "body": body,
                "result": f"From: {msg.get('from', '')}\nSubject: {msg.get('subject', '')}\n\n{body[:3000]}",
            }, None
        if name == "gmail_send_email":
            pending = self.queue_action(session, name, values)
            return {"pending_approval": pending, "result": pending}, pending
        if name == "gmail_draft_email":
            from auth import google_workspace
            result = google_workspace.draft_email(values.get("to", ""), values.get("subject", ""), values.get("body", ""))
            return result, None
        if name == "gmail_trash_email":
            pending = self.queue_action(session, name, values)
            return {"pending_approval": pending, "result": pending}, pending
        if name == "gmail_mark_read":
            from auth import google_workspace
            result = google_workspace.mark_email_read(values.get("message_id", ""), values.get("read", True))
            return result, None

        # ---- Calendar tools ----
        if name == "calendar_list_events":
            from auth import google_workspace
            result = google_workspace.list_calendar_events(days_ahead=values.get("days_ahead", 7))
            return result, None
        if name == "calendar_events_today":
            return self._call_calendar_events_today(), None
        if name == "calendar_add_event":
            from auth import google_workspace
            result = google_workspace.add_calendar_event(
                summary=values.get("summary", ""),
                start_time=values.get("start_time", ""),
                end_time=values.get("end_time", ""),
                description=values.get("description", ""),
                location=values.get("location", ""),
            )
            return result, None
        if name == "calendar_quick_add":
            from auth import google_workspace
            result = google_workspace.quick_add_calendar_event(values.get("text", ""))
            return result, None
        if name == "calendar_update_event":
            from auth import google_workspace
            result = google_workspace.update_calendar_event(
                event_id=values.get("event_id", ""),
                summary=values.get("summary", ""),
                start_time=values.get("start_time", ""),
                end_time=values.get("end_time", ""),
            )
            return result, None
        if name == "calendar_delete_event":
            pending = self.queue_action(session, name, values)
            return {"pending_approval": pending, "result": pending}, pending

        # ---- Drive tools ----
        if name == "drive_list_files":
            from auth import google_workspace
            result = google_workspace.drive_list_files(
                query=values.get("query", ""),
                max_results=values.get("max_results", 15),
            )
            return result, None
        if name == "drive_upload_file":
            from auth import google_workspace
            result = google_workspace.drive_upload_file(
                local_path=values.get("local_path", ""),
                name=values.get("name", ""),
                parent_folder=values.get("parent_folder", ""),
            )
            return result, None
        if name == "drive_download_file":
            from auth import google_workspace
            result = google_workspace.drive_download_file(
                file_id=values.get("file_id", ""),
                dest_dir=values.get("dest_dir", ""),
            )
            return result, None
        if name == "drive_create_folder":
            from auth import google_workspace
            result = google_workspace.drive_create_folder(values.get("name", ""))
            return result, None
        if name == "drive_share_file":
            from auth import google_workspace
            result = google_workspace.drive_share_file(values.get("file_id", ""))
            return result, None
        if name == "drive_delete_file":
            pending = self.queue_action(session, name, values)
            return {"pending_approval": pending, "result": pending}, pending
        if name == "drive_bulk_delete":
            pending = self.queue_action(session, name, values)
            return {"pending_approval": pending, "result": pending}, pending

        # ---- Docs tools ----
        if name == "docs_create_document":
            from auth import google_workspace
            result = google_workspace.docs_create_document(
                title=values.get("title", ""),
                text=values.get("text", ""),
            )
            return result, None
        if name == "docs_read_document":
            from auth import google_workspace
            result = google_workspace.docs_read_document(values.get("document_id", ""))
            content = result.get("content", "")
            return {
                "title": result.get("title", ""),
                "content": content,
                "result": f"Title: {result.get('title', '')}\n\n{content[:4000]}",
            }, None
        if name == "docs_append_text":
            from auth import google_workspace
            result = google_workspace.docs_append_text(
                document_id=values.get("document_id", ""),
                text=values.get("text", ""),
            )
            return result, None
        if name == "docs_replace_text":
            from auth import google_workspace
            result = google_workspace.docs_replace_text(
                document_id=values.get("document_id", ""),
                search_text=values.get("search_text", ""),
                replace_text=values.get("replace_text", ""),
            )
            return result, None

        # ---- Sheets tools ----
        if name == "sheets_create_spreadsheet":
            from auth import google_workspace
            result = google_workspace.sheets_create_spreadsheet(title=values.get("title", ""))
            return result, None
        if name == "sheets_read_range":
            from auth import google_workspace
            result = google_workspace.sheets_read_range(
                spreadsheet_id=values.get("spreadsheet_id", ""),
                range_a1=values.get("range_a1", "A1:E50"),
            )
            values_data = result.get("values", [])
            if not values_data:
                return {"result": "No values in that range."}, None
            return result, None
        if name == "sheets_write_range":
            from auth import google_workspace
            result = google_workspace.sheets_write_range(
                spreadsheet_id=values.get("spreadsheet_id", ""),
                range_a1=values.get("range_a1", ""),
                values=values.get("values", []),
            )
            return result, None
        if name == "sheets_append_rows":
            from auth import google_workspace
            result = google_workspace.sheets_append_rows(
                spreadsheet_id=values.get("spreadsheet_id", ""),
                values=values.get("values", []),
            )
            return result, None

        # ---- Google Tasks tools ----
        if name == "gtasks_list_lists":
            from auth import google_workspace
            result = google_workspace.list_task_lists()
            return result, None
        if name == "gtasks_list_tasks":
            from auth import google_workspace
            result = google_workspace.list_tasks(
                list_id=values.get("list_id", ""),
                show_completed=values.get("show_completed", False),
            )
            return result, None
        if name == "gtasks_add_task":
            from auth import google_workspace
            result = google_workspace.create_task(
                title=values.get("title", ""),
                due=values.get("due", ""),
                notes=values.get("notes", ""),
                list_id=values.get("list_id", ""),
            )
            return result, None
        if name == "gtasks_delete_task":
            pending = self.queue_action(session, name, values)
            return {"pending_approval": pending, "result": pending}, pending

        # ---- Contacts tools ----
        if name == "contacts_search":
            from auth import google_workspace
            result = google_workspace.search_contacts(values.get("query", ""))
            return result, None

        # ---- Browser tools ----
        if name == "browser_navigate":
            from browser_cdp import LiveBrowser, ensure_browser
            ensure_browser()
            browser = LiveBrowser()
            url = values.get("url", "")
            browser.navigate(url)
            title = browser.title()
            text = browser.page_text(2000)
            return {"url": url, "title": title, "page": text, "result": f"Opened {url}. Title: {title}. Page: {text[:500]}"}, None
        if name == "browser_read":
            from browser_cdp import LiveBrowser
            browser = LiveBrowser()
            data = browser.read_page(4000)
            return data, None
        if name == "browser_click":
            from browser_cdp import LiveBrowser
            browser = LiveBrowser()
            text = values.get("text", "")
            browser.click_first_link_containing(text)
            time.sleep(1.5)
            return {"result": f"Clicked link containing '{text}'."}, None
        if name == "browser_search":
            from browser_cdp import LiveBrowser, ensure_browser
            ensure_browser()
            browser = LiveBrowser()
            query = values.get("query", "")
            url = f"https://www.google.com/search?q={query}"
            browser.navigate(url)
            time.sleep(2)
            text = browser.page_text(4000)
            return {"query": query, "results": text, "result": f"Search results for '{query}':\n{text[:3000]}"}, None
        if name == "youtube_play":
            from browser_cdp import LiveBrowser, ensure_browser
            ensure_browser()
            browser = LiveBrowser()
            query = values.get("query", "")
            url = f"https://www.youtube.com/results?search_query={query}"
            browser.navigate(url)
            time.sleep(3)
            browser.click_video_result()
            return {"result": f"Playing '{query}' on YouTube."}, None

        if name == "google_maps_search":
            query = values.get("query", "")
            if not query or not query.strip():
                return {"error": "Error: a non-empty 'query' is required. Call google_maps_search again with a real business/place search like \"hair salon in Lekki\" - I cannot search without it."}, None
            from browser_cdp import LiveBrowser, ensure_browser
            try:
                ensure_browser()
                browser = LiveBrowser()
                rows = browser.search_google_maps(query, limit=values.get("limit", 10))
                if not rows:
                    return {"error": "Google Maps returned no extractable rows. The page often needs cookies accepted or a location set - open https://www.google.com/maps manually once, then retry."}, None
                lines = []
                for row in rows:
                    name = row.get("name", "")
                    rating = row.get("rating", "")
                    address = row.get("address", "")
                    link = row.get("maps_link", "")
                    lines.append(f"- {name} | {rating} | {address} | {link}")
                summary = f"Google Maps results (verbatim from the live page):\n\n" + "\n".join(lines) + "\n\nReport ONLY these rows. Do not add businesses that are not listed above."
                return {"rows": rows, "result": summary}, None
            except Exception as error:
                return {"error": f"Google Maps error: {error}"}, None

        if name == "restart_chrome_with_debug":
            try:
                from browser_cdp import ensure_browser
                ensure_browser(start_new=True, prefer_user_session=True)
                return {"result": "Chrome restarted with remote debugging port. JARVIS can now access your logged-in session.", "restarted": True}, None
            except Exception as error:
                return {"error": f"Failed to restart Chrome: {error}"}, None

        if name == "download_url":
            url = values.get("url", "")
            dest_dir = Path(os.path.expanduser("~/Downloads"))
            dest_dir.mkdir(parents=True, exist_ok=True)
            filename = url.split("/")[-1].split("?")[0] or "download"
            dest = dest_dir / filename
            try:
                import urllib.request
                urllib.request.urlretrieve(url, str(dest))
                return {"path": str(dest), "result": f"Downloaded to {dest}"}, None
            except Exception as error:
                return {"error": f"Download failed: {error}"}, None

        if name == "run_downloaded_installer":
            pending = self.queue_action(session, name, values)
            return {"pending_approval": pending, "result": pending}, pending

        if name == "write_code":
            path = values.get("path", "")
            content = values.get("content", "")
            overwrite = values.get("overwrite", False)
            target = Path(path)
            if target.exists() and not overwrite:
                return {"error": f"File exists at {path}. Set overwrite=true to replace."}, None
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                return {"result": f"Wrote {len(content)} chars to {path}"}, None
            except Exception as error:
                return {"error": f"Write failed: {error}"}, None

        # ---- Goal tools ----
        if name == "begin_task":
            return self.begin_task(session, values.get("description", ""), values.get("steps", [])), None
        if name == "goal_status":
            return self.goal_status(session), None
        if name == "goal_step":
            return self.goal_step(session, values.get("note", "")), None
        if name == "finish_goal":
            return self.finish_goal(session, values.get("summary", "")), None
        if name == "cancel_goal":
            return self.cancel_goal(session, values.get("reason", "")), None

        # ---- Skills tools ----
        if name == "list_skills":
            return self.list_skills(), None
        if name == "run_skill":
            return self.run_skill(values.get("name", ""), values.get("text", "")), None
        if name == "create_skill":
            return self.create_skill_from_description(values.get("description", "")), None

        # ---- Agent tools ----
        if name == "list_agents":
            return self.list_agents(), None
        if name == "spawn_agent":
            return self.spawn_agent(values.get("agent_id", ""), values.get("task", ""), values.get("autonomy", "medium")), None
        if name == "agent_status":
            return self.agent_status(values.get("agent_id", "")), None

        # ---- Connector tools ----
        if name == "connectors_status":
            return self.connectors_status(), None
        if name == "store_secret":
            from secrets_store import get_secrets
            vault = get_secrets()
            vault.set(values.get("service", ""), values.get("secret", ""))
            return {"result": f"Stored {values.get('service', '')} (encrypted)."}, None
        if name == "get_secret":
            from secrets_store import get_secrets
            vault = get_secrets()
            secret = vault.get(values.get("service", ""))
            if not secret:
                return {"result": f"No secret stored for {values.get('service', '')}."}, None
            return {"secret": secret, "result": f"Secret for {values.get('service', '')} retrieved. Use it and do not echo it back."}, None
        if name == "test_connector":
            from connector_oauth import test_connection
            result = test_connection(values.get("provider", ""))
            return result, None

        if name == "slack_list_channels":
            from connector_oauth import api
            data = api("slack", "GET", "https://slack.com/api/conversations.list?types=public_channel&limit=20")
            channels = [c.get("name", "") for c in data.get("channels", [])]
            return {"channels": channels, "result": f"Slack channels: {', '.join(channels)}" if channels else "No public channels found."}, None
        if name == "slack_send_message":
            from connector_oauth import api
            channel = values.get("channel", "")
            text = values.get("text", "")
            data = api("slack", "POST", f"https://slack.com/api/chat.postMessage", body={"channel": channel, "text": text})
            return {"result": f"Message sent to #{channel}."}, None

        if name == "linkedin_post_update":
            from connector_oauth import api
            text = values.get("text", "")
            try:
                payload = {
                    "author": "urn:li:person:SELF",
                    "lifecycleState": "PUBLISHED",
                    "specificContent": {
                        "com.linkedin.ugc.ShareContent": {
                            "shareCommentary": {"text": text},
                            "shareMediaCategory": "NONE",
                        }
                    },
                    "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
                }
                result = api("linkedin", "POST", "https://api.linkedin.com/rest/posts", body=payload)
                return {"result": f"LinkedIn post published: {text[:100]}"}, None
            except Exception as error:
                return {"error": f"LinkedIn error: {error}"}, None

        if name == "whatsapp_send_message":
            from connector_oauth import api, _secret
            account_id = _secret("WHATSAPP_ACCOUNT_ID") or os.getenv("WHATSAPP_ACCOUNT_ID", "")
            if not account_id:
                return {"error": "WhatsApp account ID is not configured. Store WHATSAPP_ACCOUNT_ID first (via store_secret)."}, None
            to = values.get("to", "")
            message = values.get("message", "")
            try:
                result = api("whatsapp", "POST", f"https://graph.facebook.com/v19.0/{account_id}/messages", body={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message}})
                return {"result": f"WhatsApp message sent to {to}."}, None
            except Exception as error:
                return {"error": f"WhatsApp error: {error}"}, None

        if name == "notion_query":
            from connector_oauth import api
            database_id = values.get("database_id", "")
            page_size = values.get("page_size", 20)
            try:
                result = api("notion", "POST", f"https://api.notion.com/v1/databases/{database_id}/query", body={"page_size": page_size})
                pages = result.get("results", [])
                if not pages:
                    return {"result": "No pages found in that database."}, None
                items = []
                for page in pages:
                    props = page.get("properties", {})
                    title_val = ""
                    for prop in props.values():
                        if prop.get("type") == "title":
                            title_val = "".join(t.get("plain_text", "") for t in prop.get("title", []))
                            break
                    items.append({"id": page.get("id", ""), "title": title_val})
                return {"pages": items, "result": f"Notion pages: {json.dumps(items[:10], indent=2)}"}, None
            except Exception as error:
                return {"error": f"Notion error: {error}"}, None

        if name == "notion_create_page":
            from connector_oauth import api
            database_id = values.get("database_id", "")
            title = values.get("title", "")
            content = values.get("content", "")
            try:
                body: dict[str, Any] = {
                    "parent": {"database_id": database_id},
                    "properties": {
                        "title": {"title": [{"text": {"content": title}}]}
                    },
                }
                if content:
                    body["children"] = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]}}]
                result = api("notion", "POST", "https://api.notion.com/v1/pages", body=body)
                return {"result": f"Created Notion page: {title}", "id": result.get("id", "")}, None
            except Exception as error:
                return {"error": f"Notion error: {error}"}, None

        # ---- File read/write ----
        if name == "read_file":
            path = values.get("path", "")
            target = Path(path)
            if not target.exists():
                return {"error": f"File not found: {path}"}, None
            try:
                content = target.read_text(encoding="utf-8")
                return {"content": content, "result": content[:4000]}, None
            except Exception as error:
                return {"error": f"Could not read {path}: {error}"}, None

        # ---- Approval handling ----
        if approvals:
            for action in approvals:
                if action.get("status") == "pending":
                    return {"result": "Action is awaiting explicit user approval."}, None

        return {"error": f"Unknown tool: {name}"}, None

    # ---- Goal management --------------------------------------------------

    def begin_task(self, session: str, description: str, steps: list[str] | None = None) -> dict:
        goal = {
            "description": description,
            "steps": steps or [],
            "current": 0,
            "notes": [],
            "status": "active",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        goals = self._load_goals()
        goals["active"] = goal
        self._save_goals(goals)
        return {"result": f"Started goal: {description}", "goal": goal}

    def goal_status(self, session: str) -> dict:
        goals = self._load_goals()
        active = goals.get("active")
        if not active:
            return {"result": "No active goal."}
        return {"goal": active, "result": json.dumps(active, indent=2)}

    def goal_step(self, session: str, note: str) -> dict:
        goals = self._load_goals()
        active = goals.get("active")
        if not active:
            return {"result": "No active goal."}
        active["notes"].append({"time": datetime.now(timezone.utc).isoformat(), "note": note})
        step_index = active.get("current", 0)
        if step_index < len(active.get("steps", [])):
            active["current"] = step_index + 1
        self._save_goals(goals)
        return {"result": f"Logged note on step {step_index + 1}.", "current": active["current"]}

    def finish_goal(self, session: str, summary: str = "") -> dict:
        goals = self._load_goals()
        active = goals.get("active")
        if not active:
            return {"result": "No active goal."}
        active["status"] = "completed"
        active["summary"] = summary
        active["completed_at"] = datetime.now(timezone.utc).isoformat()
        completed = goals.get("completed", [])
        completed.append(active)
        goals["completed"] = completed[-20:]
        goals.pop("active", None)
        self._save_goals(goals)
        return {"result": "Goal completed.", "goal": active}

    def cancel_goal(self, session: str, reason: str = "") -> dict:
        goals = self._load_goals()
        active = goals.get("active")
        if not active:
            return {"result": "No active goal."}
        active["status"] = "cancelled"
        active["reason"] = reason or "Cancelled by the user."
        goals.pop("active", None)
        self._save_goals(goals)
        return {"result": "Cancelled by the user."}

    def _load_goals(self) -> dict:
        try:
            if self.goals_path.exists():
                return json.loads(self.goals_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_goals(self, goals: dict) -> None:
        try:
            self.goals_path.write_text(json.dumps(goals, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    # ---- Skills -----------------------------------------------------------

    def list_skills(self) -> dict:
        skills = self.skills.all()
        if not skills:
            return {"result": "No skills installed.", "skills": []}
        lines = [f"- {s['name']}: {s.get('description', '')}" for s in skills]
        return {"skills": skills, "result": "\n".join(lines)}

    def run_skill(self, name: str, text: str = "") -> dict:
        skill = self.skills.get(name)
        if not skill:
            skill_names = [s["name"] for s in self.skills.all()]
            return {"error": f"Unknown skill '{name}'. Available: {', '.join(skill_names)}"}
        if not skill.get("enabled", True):
            return {"error": f"Skill '{name}' is disabled."}
        outputs: list[str] = []
        for step in skill.get("steps", []):
            tool_name = step.get("tool", "")
            values = step.get("values", {})
            if "text" not in values and text:
                values["text"] = text
            result, _ = self.call_tool(tool_name, values)
            outputs.append(str(result.get("result", result)) if isinstance(result, dict) else str(result))
        description = skill.get("description", name)
        return {"description": description, "values": skill.get("steps", []), "result": "\n".join(outputs) or f"Skill '{name}' executed."}

    def create_skill(self, name: str, description: str, triggers: list[str], prompt: str = "", steps: list | None = None, script: str = "") -> dict:
        skill_data = {
            "name": name,
            "description": description,
            "enabled": True,
            "triggers": triggers,
            "prompt": prompt,
            "steps": steps or [],
            "script": script,
        }
        skill_path = BASE_DIR / "skills" / f"{name}.json"
        try:
            skill_path.write_text(json.dumps(skill_data, indent=2, ensure_ascii=False), encoding="utf-8")
            self.skills.load()
            return {"result": f"Skill '{name}' created.", "skill": skill_data}
        except Exception as error:
            return {"error": f"Could not create skill: {error}"}

    def create_skill_from_description(self, description: str) -> dict:
        prompt = (
            "You are the JARVIS skill composer. Convert the description into a valid skill JSON.\n"
            "Available tools: " + ", ".join(t["function"]["name"] for t in self.tools()) + ".\n\n"
            'JSON schema:\n{\n  "name": "snake_case",\n  "description": "one line",\n  "enabled": true,\n'
            '  "triggers": ["phrase 1", "phrase 2"],\n  "prompt": "extra system guidance for the model",\n'
            '  "steps": [{"tool": "tool_name", "values": {"param": "value"}}],\n  "script": ""\n}\n'
            '"steps" may be empty; then the skill acts as a prompt-only capability.\n'
            "Reply with ONLY valid JSON."
        )
        try:
            content = self._llm_text(description, system=prompt, max_tokens=1000)
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```\w*\n?", "", content)
                content = re.sub(r"\n?```$", "", content)
            data = json.loads(content)
            if not isinstance(data, dict) or not data.get("name"):
                return {"error": "Could not compose a valid skill from that description."}
            name = re.sub(r"[^a-z0-9_]", "_", data.get("name", "").lower().strip())
            data["name"] = name
            data.setdefault("description", description[:200])
            data.setdefault("enabled", True)
            data.setdefault("triggers", [])
            data.setdefault("prompt", "")
            data.setdefault("steps", [])
            data.setdefault("script", "")
            skill_path = BASE_DIR / "skills" / f"{name}.json"
            skill_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            self.skills.load()
            return {"result": f"Skill '{name}' composed and ready: {data.get('description', '')}", "skill": data}
        except Exception as error:
            return {"error": f"Skill composition failed: {error}"}

    # ---- Agents -----------------------------------------------------------

    def list_agents(self) -> list[dict]:
        hub = self.agent_hub
        if not hub:
            return []
        return hub.list_agents()

    def spawn_agent(self, agent_id: str, task: str, autonomy: str = "medium") -> dict:
        hub = self.agent_hub
        if not hub:
            return {"error": "AgentHub not initialized."}
        agent = hub.get(agent_id)
        if not agent:
            available = ", ".join(a.config.id for a in hub.agents.values())
            return {"error": f"Unknown agent: {agent_id}. Available: {available}"}
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(hub.spawn(agent_id, task, autonomy))
            finally:
                loop.close()
            return {"result": f"Agent return: {result}", "agent_result": result}
        except Exception as error:
            return {"error": f"Agent failed: {error}"}

    def spawn_agent_async(self, agent_id: str, task: str, autonomy: str = "medium") -> dict:
        """Launch an agent run in the background and return immediately with a run_id
        so the UI can show live progress."""
        hub = self.agent_hub
        if not hub:
            return {"error": "AgentHub not initialized."}
        agent = hub.get(agent_id)
        if not agent:
            available = ", ".join(a.config.id for a in hub.agents.values())
            return {"error": f"Unknown agent: {agent_id}. Available: {available}"}
        run_id = f"agent-{str(uuid.uuid4())[:8]}"
        _uuid = run_id
        _dt = datetime.now(timezone.utc)

        from agent_hub import AgentRun
        pending_run = AgentRun(
            id=_uuid,
            agent_id=agent_id,
            task=task,
            status="pending",
            started_at=_dt,
        )
        agent.runs[_uuid] = pending_run
        agent._save_runs()

        def worker():
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(hub.spawn(agent_id, task, autonomy))
                finally:
                    loop.close()
            except Exception as error:
                log.error(f"[AGENT-ASYNC] run {_uuid} crashed: {error}")

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        return {"run_id": _uuid, "agent_id": agent_id, "status": "running", "result": f"Agent launched: {agent_id} (run {_uuid})"}

    def agent_status(self, agent_id: str) -> dict:
        hub = self.agent_hub
        if not hub:
            return {"error": "AgentHub not initialized."}
        agent = hub.get(agent_id)
        if not agent:
            return {"error": f"Unknown agent: {agent_id}"}
        runs = sorted(agent.runs.values(), key=lambda r: r.started_at, reverse=True)
        if not runs:
            return {"agent_id": agent_id, "status": "idle", "runs": [], "result": f"Agent '{agent_id}' has no runs yet."}
        lines = [f"{r.id} | {r.status} | {r.task[:60]} | {r.started_at.strftime('%H:%M')}" for r in runs[:10]]
        return {
            "agent_id": agent_id,
            "status": runs[0].status if runs else "idle",
            "runs": [{"id": r.id, "status": r.status, "task": r.task, "started_at": r.started_at.isoformat()} for r in runs[:10]],
            "result": f"Agent '{agent_id}' - {len(runs)} runs:\n" + "\n".join(lines),
        }

    # ---- File operations --------------------------------------------------

    def download_url(self, url: str, dest_dir: str = "") -> dict:
        toolkit_downloads = Path(dest_dir) if dest_dir else Path(os.path.expanduser("~/Downloads"))
        toolkit_downloads.mkdir(parents=True, exist_ok=True)
        filename = url.split("/")[-1].split("?")[0] or "download"
        dest = toolkit_downloads / filename
        try:
            import urllib.request
            urllib.request.urlretrieve(url, str(dest))
            return {"path": str(dest), "result": f"Downloaded to {dest}"}
        except Exception as error:
            return {"error": f"Download failed: {error}"}

    def write_code(self, path: str, content: str, language: str = "", overwrite: bool = False) -> dict:
        toolkit_downloads = Path(path)
        if toolkit_downloads.exists() and not overwrite:
            return {"error": f"File exists at {path}. Set overwrite=true to replace."}
        try:
            toolkit_downloads.parent.mkdir(parents=True, exist_ok=True)
            toolkit_downloads.write_text(content, encoding="utf-8")
            return {"result": f"Wrote {len(content)} chars to {path}"}
        except Exception as error:
            return {"error": f"Write failed: {error}"}

    # ---- LLM integration -------------------------------------------------

    def _llm_text(self, user_text: str, system: str = "", max_tokens: int = 1024, temperature: float = 0.3) -> str:
        """Single non-tool LLM response for skill composition and simple paths."""
        if self._provider == "groq":
            from groq import Groq
            client = Groq(api_key=os.getenv("GROQ_API_KEY"))
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": user_text})
            response = client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response.choices[0].message.content or ""
        elif self._provider == "gemini":
            import google.generativeai as genai
            genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
            model = genai.GenerativeModel(self._model, system_instruction=system if system else None)
            response = model.generate_content(user_text, generation_config=genai.types.GenerationConfig(max_output_tokens=max_tokens, temperature=temperature))
            return response.text or ""
        raise AssistantUnavailableError(f"No LLM provider available ({self._provider}).")

    def _gemini_tools(self, tools: list[dict]) -> list[dict]:
        converted = []
        for t in tools:
            fn = t.get("function", {})
            params = fn.get("parameters", {})
            converted.append({
                "function_declarations": [{
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": self._convert_schema(params),
                }]
            })
        return converted

    def _serialize_call_args(self, value: Any, json_format: bool = False) -> str:
        """Serialize a Gemini FunctionCall.args (MapComposite, protobuf Struct or
        plain dict) into a JSON string, whatever shape the SDK hands us."""
        def convert(item: Any) -> Any:
            if isinstance(item, dict):
                return {k: convert(v) for k, v in item.items()}
            if isinstance(item, (list, tuple)):
                return [convert(i) for i in item]
            if hasattr(item, "items"):
                return {k: convert(v) for k, v in item.items()}
            return item
        result = convert(value)
        return json.dumps(result, ensure_ascii=False)

    def _convert_schema(self, schema: dict) -> dict:
        type_map = {
            "object": "OBJECT",
            "string": "STRING",
            "integer": "INTEGER",
            "number": "NUMBER",
            "boolean": "BOOLEAN",
        }
        converted: dict[str, Any] = {}
        props = schema.get("properties", {})
        required = schema.get("required", [])
        if props:
            converted["properties"] = {}
            for k, v in props.items():
                prop: dict[str, Any] = {"type": type_map.get(v.get("type", "string"), "STRING")}
                if v.get("description"):
                    prop["description"] = v["description"]
                converted["properties"][k] = prop
        if required:
            converted["required"] = required
        converted["type"] = type_map.get(schema.get("type", "object"), "OBJECT")
        return converted

    # ---- Fast path (reflexes) ---------------------------------------------

    def _fast_path(self, text: str) -> dict | None:
        """Handle instant reflexes without an LLM round trip."""
        data: dict[str, Any] = {}
        lowered = text.lower().strip()

        import re as _re
        volume_match = _re.search(r"volume (up|down)|(?:set (?:the )?volume (?:to )?(\\d{1,3})|volume (\\d{1,3}))|mute|unmute", lowered)
        if volume_match:
            data = {"name": "control_pc", "arguments": {"action": "volume"}, "values": {}}
            if "mute" in lowered or "unmute" in lowered:
                data["arguments"]["action"] = "mute_volume"
                data["values"] = {"action": "mute_volume", "default": True}
            elif volume_match.group(1) == "up":
                data["arguments"]["action"] = "volume_step"
                data["values"] = {"action": "volume_step", "delta": 10}
            elif volume_match.group(1) == "down":
                data["arguments"]["action"] = "volume_step"
                data["values"] = {"action": "volume_step", "delta": -10}
            elif volume_match.group(2):
                data["arguments"]["action"] = "set_volume"
                data["values"] = {"action": "set_volume", "volume": int(volume_match.group(2))}
            elif volume_match.group(3):
                data["arguments"]["action"] = "set_volume"
                data["values"] = {"action": "set_volume", "volume": int(volume_match.group(3))}
            else:
                data["arguments"]["action"] = "get_volume"
                data["values"] = {"action": "get_volume"}
            return data

        media_match = _re.search(r"play|pause|next|previous|forward|backward", lowered)
        if media_match:
            action = media_match.group(0)
            if action in ("forward", "backward"):
                action = "next" if action == "forward" else "previous"
            data = {"name": "media_control", "arguments": {"action": "play_pause" if action in ("play", "pause") else action}, "values": {"action": "play_pause" if action in ("play", "pause") else action}}
            return data

        _re = _re  # keep in scope
        if "open chat" in lowered or "open the chat" in lowered or "open jarvis chat" in lowered:
            return {"name": "quick_pc_action", "arguments": {"action": "open_chat"}, "values": {"action": "open_chat"}, "result": "Opening the chat console."}
        if "close chat" in lowered or "close the chat" in lowered:
            return {"name": "quick_pc_action", "arguments": {"action": "close_chat"}, "values": {"action": "close_chat"}, "result": "Closing the chat console."}

        return None

    # ---- Main execution loop ----------------------------------------------

    def execute(self, text: str, session: str = "default") -> dict[str, Any]:
        confirmations: list[dict] = []
        results: list[dict] = []

        if not text or not text.strip():
            return {"reply": "Say something first.", "results": []}

        skill = self.skills.by_trigger(text)
        memory_count = self.memory.count()
        skills = self.skills.all()

        # Build memories context string
        recent_memories = self.memory.search("", limit=10)
        memories_str = "\n".join(f"- {m.get('content', '')}" for m in recent_memories) if recent_memories else ""

        # Active goal context
        goals = self._load_goals()
        active_goal = goals.get("active")
        goal_str = ""
        if active_goal:
            goal_str = f"\nActive goal:\n{json.dumps(active_goal, indent=2)}"

        # Skill guidance
        skill_guidance = ""
        if skill:
            if skill.get("prompt"):
                skill_guidance = f"\nSkill guidance: {skill['prompt']}"
            # Execute skill steps automatically
            for step in skill.get("steps", []):
                tool_name = step.get("tool", "")
                values = step.get("values", {})
                if tool_name and values:
                    result, pending = self.call_tool(tool_name, values, session)
                    results.append(result)

        # Agents context
        agents_str = ""
        hub = self.agent_hub
        if hub:
            agent_list = [f"{a.config.id} ({a.config.name})" for a in hub.agents.values()]
            agents_str = "\nAvailable agents: " + ", ".join(agent_list) + ". When the user asks to run/use an agent (e.g. 'run lead hunt', 'use the recruiter', 'deploy inbox zero'), call spawn_agent with the best-matching agent_id and a concrete task. Then summarize the result it returns.\n"

        system = (
            "You are JARVIS - a local AI assistant with tools for this user's PC, Google Workspace "
            "(Gmail/Calendar/Drive/Docs/Sheets/Tasks/Contacts), browser automation, memory, goals, "
            "skills, and specialized agents.\n"
            f"Current date: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}.\n"
            "Be concise and decisive. When a result is long (a full email scan, report, multi-step "
            "summary) write the full content by setting the special tool 'present_long_result'? No - "
            "instead, when finished, reply briefly and the system will show the answer on screen "
            "automatically for long output.\n"
            "If a protected tool is called (gmail_send_email, calendar_delete_event, drive_delete_file, "
            "drive_bulk_delete, gtasks_delete_task, run_downloaded_installer, gmail_trash_email), always "
            "call it - the approval system will handle user confirmation.\n\n"
            "TRUTHFULNESS RULES:\n"
            "- NEVER fabricate tool results. Only report what the tools actually returned.\n"
            "- If a tool returns an error, say so. Do not pretend it succeeded.\n"
            "- If you don't have enough information, ask the user. Do not guess.\n"
            "- For Google Maps, business directories, or any data extraction: only report rows that the "
            "tool actually returned. Never invent businesses, names, ratings, or addresses.\n"
            "- If google_maps_search returns no results, say 'No results found' - do NOT make up data.\n\n"
            "BROWSER BEHAVIOR:\n"
            "- browser_navigate reuses the CURRENT tab. It does NOT create new tabs.\n"
            "- Use browser_navigate to switch between sites (YouTube -> Google Maps -> YouTube).\n"
            "- Do NOT use browser_navigate if you just want to read the current page (use browser_read).\n"
            "- For Chrome session mode: JARVIS works in YOUR Chrome with your logged-in accounts. "
            "To restart Chrome with debug port, call restart_chrome_with_debug.\n\n"
            "CONTEXT AWARENESS:\n"
            "- You are answering in the JARVIS chat UI, NOT directly in the browser.\n"
            "- When the user says 'search X' or 'look up X', use browser_search or browser_navigate - "
            "do NOT say 'I'll search in the browser' and then do nothing.\n"
            "- When you use a browser tool, immediately report what you found.\n"
            f"\nRelevant memories:\n{memories_str}"
            f"{goal_str}"
            f"{skill_guidance}"
            f"{agents_str}"
        )

        # Build messages
        messages: list[dict] = [{"role": "system", "content": system}]
        history = self._sessions.get(session, [])
        for msg in history[-20:]:
            messages.append(msg)
        messages.append({"role": "user", "content": text})

        # Fast path check
        fast = self._fast_path(text)
        if fast and fast.get("result"):
            return {"reply": fast["result"], "results": [fast]}

        # LLM tool loop
        blocked_count: dict[str, int] = {}
        max_iterations = 10
        for _iteration in range(max_iterations):
            if self._provider == "gemini":
                response_text, tool_calls, finish = self._gemini_turn(system, messages, text)
            else:
                response_text, tool_calls, finish = self._groq_turn(messages)

            if not tool_calls:
                # Store conversation
                self._sessions.setdefault(session, []).append({"role": "user", "content": text})
                if response_text:
                    self._sessions.setdefault(session, []).append({"role": "assistant", "content": response_text})
                return {"reply": response_text, "results": results, "chat_text": response_text}

            # Execute tool calls
            for tc in tool_calls:
                fn_name = tc.get("name", tc.get("function", {}).get("name", ""))
                fn_args = tc.get("arguments", tc.get("function", {}).get("arguments", {}))
                if isinstance(fn_args, str):
                    try:
                        fn_args = json.loads(fn_args)
                    except Exception:
                        fn_args = {}

                # Block loop repetition
                block_key = f"{fn_name}:{json.dumps(fn_args, sort_keys=True)}"
                blocked_count[block_key] = blocked_count.get(block_key, 0) + 1
                if blocked_count[block_key] > 2:
                    results.append({"error": f"BLOCKED: you already performed this exact action {blocked_count[block_key]} times in this command. Do NOT repeat it. Summarize what was done and answer now."})
                    continue

                log.info(f"[EXECUTE] {fn_name}({json.dumps(fn_args)[:200]})")
                try:
                    result, pending = self.call_tool(fn_name, fn_args, session, confirmations)
                    results.append(result)
                    if pending:
                        confirmations.append(pending)
                except Exception as error:
                    error_msg = str(error)
                    if "rate_limit" in error_msg.lower() or "429" in error_msg:
                        results.append({"error": f"[EXECUTE] Error with {fn_name}: {error_msg}. limit hit on {fn_name}. Switch to another model in the dropdown and try again."})
                    else:
                        results.append({"error": f"[EXECUTE] Error with {fn_name}: {error_msg}"})

            # Build follow-up message with tool results
            tool_results_text = "\n".join(
                f"{fn_name}: {json.dumps(r)[:500]}" for r, fn_name in zip(results, [tc.get("name", "") for tc in tool_calls])
            )
            messages.append({"role": "assistant", "content": f"Tool results:\n{tool_results_text}"})

        # Build final chat text
        chat_text = "Done - I've written it all in the chat."
        if results:
            last = results[-1] if results else {}
            if isinstance(last, dict) and "result" in last:
                chat_text = str(last["result"])
            elif isinstance(last, dict) and "error" in last:
                chat_text = str(last["error"])

        return {"reply": chat_text, "results": results, "chat_text": chat_text, "confirmations": confirmations}

    def _groq_turn(self, messages: list[dict]) -> tuple[str, list[dict], str]:
        from groq import Groq
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        response = client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=self.tools(),
            tool_choice="auto",
            max_tokens=4096,
        )
        message = response.choices[0].message
        reply = message.content or ""
        tool_calls = []
        finish = response.choices[0].finish_reason or ""
        if message.tool_calls:
            for tc in message.tool_calls:
                fn = tc.function
                try:
                    args = json.loads(fn.arguments)
                except Exception:
                    args = {}
                tool_calls.append({"name": fn.name, "arguments": args})
        return reply, tool_calls, finish

    def _gemini_execute(self, system_prompt: str, prior_messages: list[dict], text: str, session: str, approvals: list, data: dict) -> dict:
        """Gemini tool loop using a persistent chat: function responses are sent
        as follow-up messages and each reply may continue with more calls."""
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
        model = genai.GenerativeModel(
            self._model,
            system_instruction=system_prompt,
            tools=self._gemini_tools(self.tools()),
        )
        chat = model.start_chat(history=[])
        for msg in prior_messages:
            if msg.get("role") == "user":
                chat.history.append(genai.types.Content(role="user", parts=[genai.types.Part(text=msg["content"])]))
            elif msg.get("role") == "assistant":
                chat.history.append(genai.types.Content(role="model", parts=[genai.types.Part(text=msg.get("content", ""))]))

        response = chat.send_message(text, generation_config=genai.types.GenerationConfig(max_output_tokens=4096))
        results: list[dict] = []
        confirmations: list[dict] = []

        while True:
            tool_calls = []
            reply_text = ""
            for part in response.candidates[0].content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    args_str = self._serialize_call_args(fc.args)
                    try:
                        args = json.loads(args_str)
                    except Exception:
                        args = {}
                    tool_calls.append({"name": fc.name, "arguments": args})
                elif hasattr(part, "text") and part.text:
                    reply_text += part.text

            if not tool_calls:
                return {"reply": reply_text, "results": results, "chat_text": reply_text}

            function_responses = []
            for tc in tool_calls:
                fn_name = tc["name"]
                fn_args = tc["arguments"]
                try:
                    result, pending = self.call_tool(fn_name, fn_args, session, confirmations)
                    results.append(result)
                    if pending:
                        confirmations.append(pending)
                except Exception as error:
                    result = {"error": str(error)}
                    results.append(result)
                function_responses.append(genai.types.Part(function_response=genai.types.FunctionResponse(
                    name=fn_name,
                    response=result if isinstance(result, dict) else {"result": str(result)},
                )))
            response = chat.send_message(function_responses)

        return {"reply": "", "results": results}

    def _gemini_turn(self, system: str, messages: list[dict], text: str) -> tuple[str, list[dict], str]:
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
        model = genai.GenerativeModel(
            self._model,
            system_instruction=system,
            tools=self._gemini_tools(self.tools()),
        )
        chat = model.start_chat(history=[])
        for msg in messages[:-1]:
            role = "user" if msg.get("role") == "user" else "model"
            chat.history.append(genai.types.Content(role=role, parts=[genai.types.Part(text=msg.get("content", ""))]))

        response = chat.send_message(text, generation_config=genai.types.GenerationConfig(max_output_tokens=4096))
        tool_calls = []
        reply_text = ""
        for part in response.candidates[0].content.parts:
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                args_str = self._serialize_call_args(fc.args)
                try:
                    args = json.loads(args_str)
                except Exception:
                    args = {}
                tool_calls.append({"name": fc.name, "arguments": args})
            elif hasattr(part, "text") and part.text:
                reply_text += part.text
        return reply_text, tool_calls, "stop" if not tool_calls else "tool_calls"

    # ---- Confirmation handling --------------------------------------------

    def confirm(self, action_id: str, approved: bool, session: str) -> dict:
        actions = self._sessions.get(session, [])
        action = None
        for a in actions:
            if a.get("id") == action_id:
                action = a
                break
        if not action:
            return {"error": "Unknown protected action."}
        expires = action.get("expires", 0)
        if time.time() > expires:
            return {"error": "This approval has expired. Ask Jarvis again."}
        if not approved:
            action["status"] = "cancelled"
            return {"result": "Action cancelled."}
        action["status"] = "approved"
        fn = action.get("action", {})
        name = fn.get("name", "")
        values = fn.get("values", {})
        try:
            result, _ = self.call_tool(name, values, session)
            return result
        except Exception as error:
            return {"error": f"Action failed: {error}"}

    def speak_aloud(self, text: str) -> dict:
        return {"spoken": text}


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service: JarvisService | None = None


def get_jarvis_service() -> JarvisService:
    global _service
    if _service is None:
        _service = JarvisService()
    return _service
