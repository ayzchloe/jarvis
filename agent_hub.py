#!/usr/bin/env python3
"""
Agent Hub — Persistent, specialized money-making agents with personas.
Each agent has a personality, dedicated tools, persistent memory, and can run autonomously.
"""
import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, TYPE_CHECKING

import sys
sys.path.insert(0, str(Path(__file__).parent))

if TYPE_CHECKING:
    from ai_service import JarvisService
from browser_tools import BrowserTools

log = logging.getLogger("jarvis.agents")

AGENTS_DIR = Path(__file__).resolve().parent / "data" / "agents"
MAX_MEMORY_NOTES = 40
MAX_SAVED_RUNS = 20


class AutonomyLevel(str, Enum):
    LOW = "low"       # Ask before each action
    MEDIUM = "medium" # Act within defined scope, report results
    HIGH = "high"     # Full autonomy within guardrails


@dataclass
class AgentConfig:
    id: str
    name: str
    persona: str
    system_prompt: str
    tools: List[str] = field(default_factory=list)
    triggers: List[str] = field(default_factory=list)
    schedule: Optional[str] = None  # cron expression
    autonomy: AutonomyLevel = AutonomyLevel.MEDIUM
    memory_scope: str = "persistent"  # "session" or "persistent"
    tools_config: Dict = field(default_factory=dict)
    guardrails: List[str] = field(default_factory=list)


@dataclass
class AgentRun:
    id: str
    agent_id: str
    task: str
    status: str  # "pending", "running", "completed", "failed"
    started_at: datetime
    completed_at: Optional[datetime] = None
    result: Optional[str] = None
    error: Optional[str] = None
    steps: List[Dict] = field(default_factory=list)


class Agent:
    """A specialized agent with personality, tools, and persistent memory."""

    def __init__(self, config: AgentConfig, jarvis: 'JarvisService', browser: 'BrowserTools') -> None:
        self.config = config
        self.jarvis = jarvis
        self.browser = browser
        self.memory: List[Dict] = []
        self.runs: Dict[str, AgentRun] = {}
        self._running = False
        self._memory_path = AGENTS_DIR / f"{self.config.id}_memory.json"
        self._runs_path = AGENTS_DIR / f"{self.config.id}_runs.json"
        self._load_persisted()

    # ---- persistence -----------------------------------------------------
    def _load_persisted(self) -> None:
        try:
            if self._memory_path.exists():
                data = json.loads(self._memory_path.read_text(encoding="utf-8"))
                raw = data.get("notes", []) if isinstance(data, dict) else data
                self.memory = [m for m in raw if isinstance(m, dict) and m.get("note")]
        except Exception as error:
            log.warning(f"[{self.config.id}] could not load memory: {error}")
        try:
            if self._runs_path.exists():
                raw = json.loads(self._runs_path.read_text(encoding="utf-8"))
                for item in raw:
                    try:
                        run = AgentRun(
                            id=item.get("id", ""),
                            agent_id=item.get("agent_id", self.config.id),
                            task=item.get("task", ""),
                            status=item.get("status", "failed"),
                            started_at=datetime.fromisoformat(item["started_at"]),
                            completed_at=datetime.fromisoformat(item["completed_at"]) if item.get("completed_at") else None,
                            result=item.get("result"),
                            error=item.get("error"),
                            steps=item.get("steps", []),
                        )
                        self.runs[run.id] = run
                    except Exception:
                        continue
        except Exception as error:
            log.warning(f"[{self.config.id}] could not load runs: {error}")

    def _save_memory(self) -> None:
        try:
            AGENTS_DIR.mkdir(parents=True, exist_ok=True)
            self._memory_path.write_text(
                json.dumps({"agent_id": self.config.id, "notes": self.memory}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as error:
            log.warning(f"[{self.config.id}] could not save memory: {error}")

    def _save_runs(self) -> None:
        try:
            AGENTS_DIR.mkdir(parents=True, exist_ok=True)
            recent = list(self.runs.values())[-MAX_SAVED_RUNS:]
            self._runs_path.write_text(
                json.dumps([{
                    "id": r.id, "agent_id": r.agent_id, "task": r.task, "status": r.status,
                    "started_at": r.started_at.isoformat(),
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                    "result": r.result, "error": r.error, "steps": r.steps,
                } for r in recent], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as error:
            log.warning(f"[{self.config.id}] could not save runs: {error}")

    def remember(self, note: str) -> None:
        """Store a persistent note so knowledge survives across sessions."""
        self.memory.append({"ts": datetime.now().isoformat(timespec="seconds"), "note": note})
        self.memory = self.memory[-MAX_MEMORY_NOTES:]
        self._save_memory()

    def memory_context(self, limit: int = 12) -> str:
        """Render recent memory notes for injection into the agent's prompt."""
        if not self.memory:
            return ""
        lines = [f"- [{m['ts']}] {m['note']}" for m in self.memory[-limit:]]
        return "Your persistent memory from past runs:\n" + "\n".join(lines)

    async def run(self, task: str, autonomy: str = "medium", run_id: Optional[str] = None) -> str:
        """Execute a task with this agent's persona and tools."""
        run_id = run_id or str(uuid.uuid4())[:8]
        run = AgentRun(
            id=run_id,
            agent_id=self.config.id,
            task=task,
            status="running",
            started_at=datetime.now()
        )
        self.runs[run_id] = run

        log.info(f"[{self.config.id}] Starting run {run_id}: {task}")

        try:
            # Build agent-specific context
            context = self._build_context(task)

            # Execute using JARVIS core with agent's tools
            result = await self._execute_with_tools(task)

            run.status = "completed"
            run.completed_at = datetime.now()
            run.result = result
            self.remember(f"Task done: {task[:160]} — {result[:220]}")
            self._save_runs()
            log.info(f"[{self.config.id}] Run {run_id} completed")
            return result

        except Exception as e:
            run.status = "failed"
            run.error = str(e)
            run.completed_at = datetime.now()
            self.remember(f"Failed: {task[:160]} — {str(e)[:180]}")
            self._save_runs()
            log.error(f"[{self.config.id}] Run {run_id} failed: {e}")
            raise

    def _build_context(self, task: str) -> Dict:
        return {
            "agent": self.config.name,
            "persona": self.config.persona,
            "task": task,
            "memory": self.memory,
            "timestamp": datetime.now().isoformat()
        }

    async def _execute_with_tools(self, task: str) -> str:
        """Execute task using agent's dedicated tools via JARVIS core."""
        if self.jarvis is None:
            return "Agent hub is not wired to the JARVIS backend."
        instructions = (
            f"You are {self.config.name}.\n"
            f"Persona: {self.config.persona}\n\n"
            f"Mission: {self.config.system_prompt}\n\n"
            f"Tool guidance: prefer these tools when relevant — {', '.join(self.config.tools)}.\n\n"
            f"{self.memory_context()}\n\n"
            f"Execute the following task end-to-end, using tools as needed, and "
            f"report the final result (structured data where appropriate): {task}"
        )
        result = self.jarvis.execute(instructions, session=f"agent-{self.config.id}")
        reply = str(result.get("reply", "")).strip()
        full = result.get("chat_text")
        if full:
            reply = (reply + "\n\n" + full).strip()
        return reply or "Agent finished without a readable result."


class AgentHub:
    """Central registry and lifecycle manager for all specialized agents."""

    def __init__(self, jarvis: Any, browser: 'BrowserTools'):
        self.jarvis = jarvis
        self.browser = browser
        self.agents: Dict[str, Agent] = {}
        self._load_builtin_agents()
        for agent in self.agents.values():
            agent.jarvis = jarvis
            agent.browser = browser

    def _load_builtin_agents(self):
        """Register all built-in money-making agents."""

        # ──────────────────────────────────────────────────────────────────
        # RECRUITER RYAN — Upwork/LinkedIn sourcing, outreach, tracking
        # ──────────────────────────────────────────────────────────────────
        self.register(AgentConfig(
            id="recruiter_ryan",
            name="Recruiter Ryan",
            persona=(
                "You are Ryan, a senior technical recruiter who has placed 200+ engineers "
                "at top startups. You're sharp, efficient, and treat every search like a "
                "targeted mission. You know exactly how to craft outreach that gets replies, "
                "how to evaluate technical talent from GitHub/Upwork profiles, and how to "
                "manage a pipeline from sourcing to signed offer. You speak professionally "
                "but with a hint of swagger — you know you're good at this."
            ),
            system_prompt=(
                "You are Recruiter Ryan, a senior technical recruiter. "
                "Your mission: find, vet, and engage top technical talent on Upwork and LinkedIn. "
                "Tools at your disposal: browser_navigate, browser_extract, browser_loop, "
                "upwork_search, linkedin_connect, gmail_send_email, calendar_add_event. "
                "Always output structured data (JSON/CSV) for pipeline tracking. "
                "Never send generic outreach — every message is personalized to the candidate's work."
            ),
            tools=[
                "browser_navigate", "browser_extract", "browser_loop",
                "upwork_search", "linkedin_search", "linkedin_connect",
                "gmail_send_email", "calendar_add_event", "write_file"
            ],
            triggers=["recruit", "hire", "find developer", "upwork", "linkedin recruitment"],
            autonomy=AutonomyLevel.HIGH,
            guardrails=[
                "Never send spam — every outreach is personalized",
                "Respect rate limits (max 20 connection requests/day on LinkedIn)",
                "Never share candidate data externally without consent",
                "Track all outreach in CSV for compliance"
            ]
        ))

        # ──────────────────────────────────────────────────────────────────
        # INVOICE IVY — Invoice scanning, chasing, reconciliation
        # ──────────────────────────────────────────────────────────────────
        self.register(AgentConfig(
            id="invoice_ivy",
            name="Invoice Ivy",
            persona=(
                "You are Ivy, a ruthlessly organized accounts-receivable specialist who "
                "has never let an invoice go unpaid past 30 days. You're polite but "
                "relentless — you know exactly when to escalate, when to call, when to "
                "send the final notice. You track every invoice from creation to cash-in-bank, "
                "and you reconcile against bank statements like a hawk. You speak professionally "
                "but with zero tolerance for late payers."
            ),
            system_prompt=(
                "You are Invoice Ivy, an AR specialist. Mission: zero invoices past 30 days. "
                "Tools: gmail_read_emails (query: invoice), gmail_read_email, "
                "gmail_send_email, drive_list_files, drive_download_file, "
                "sheets_write_range, sheets_read_range, calendar_add_event. "
                "Workflow: scan inbox for invoices → extract data → track in sheet → "
                "send reminders at 7/14/30 days → escalate to phone/legal at 45/60 days → "
                "reconcile against bank CSV monthly. Output: aging report, collection log."
            ),
            tools=[
                "gmail_read_emails", "gmail_read_email", "gmail_send_email",
                "drive_list_files", "drive_download_file",
                "sheets_write_range", "sheets_read_range", "calendar_add_event",
                "write_file", "read_file"
            ],
            triggers=["invoice", "collect payment", "accounts receivable", "chase payment", "unpaid"],
            autonomy=AutonomyLevel.HIGH,
            guardrails=[
                "Never send threatening language — professional escalation only",
                "Respect payment terms (Net 15/30/45) before escalating",
                "Log every communication in collection tracker",
                "Never delete or modify original invoices"
            ]
        ))

        # ──────────────────────────────────────────────────────────────────
        # LEAD HUNTER — IG/LinkedIn lead mining, enrichment, CSV export
        # ──────────────────────────────────────────────────────────────────
        self.register(AgentConfig(
            id="lead_hunter",
            name="Lead Hunter",
            persona=(
                "You are a precision lead generation specialist who treats every search "
                "like a reconnaissance mission. You know exactly which hashtags, keywords, "
                "and filters surface high-intent prospects on Instagram and LinkedIn. "
                "You enrich with email finders, score by intent signals, and deliver "
                "clean, deduplicated CSVs ready for outreach. You're methodical, thorough, "
                "and never return garbage leads."
            ),
            system_prompt=(
                "You are Lead Hunter. Mission: find high-intent prospects on Google Maps, "
                "Instagram/LinkedIn, enrich with verified contact info, deliver clean CSV. "
                "Tools: google_maps_search (REAL live Google Maps results — use this first), "
                "browser_navigate, browser_loop, browser_extract, "
                "linkedin_search, instagram_search, hunter_enrich, write_file. "
                "Process: define ICP → google_maps_search for local businesses (e.g. "
                "'bakery in Lagos') → integrate with competitors/missing-website signals "
                "from the returned rows → enrich emails → deduplicate → score by intent → "
                "export CSV with columns: name, platform, profile_url, address, rating, "
                "website, score, notes. "
                "CALLING google_maps_search: always pass BOTH arguments, e.g. "
                "google_maps_search(query=\"hair salon in Lekki\", limit=10). "
                "A call without a non-empty query will be rejected, so retry with the query filled in."
                "CRITICAL: only include businesses that google_maps_search actually returned. "
                "Never invent or guess a business. If the tool returns nothing, say so."
            ),
            tools=[
                "google_maps_search", "browser_navigate", "browser_loop", "browser_extract",
                "linkedin_search", "instagram_search", "write_file"
            ],
            triggers=["find leads", "lead generation", "instagram leads", "linkedin leads", "prospect"],
            autonomy=AutonomyLevel.MEDIUM,
            guardrails=[
                "Respect platform ToS — no aggressive scraping",
                "Rate limit: max 30 profile views/minute",
                "Only extract public data — no private info",
                "Deduplicate against existing CRM exports"
            ]
        ))

        # ──────────────────────────────────────────────────────────────────
        # INBOX ZERO — Cross-platform triage (Gmail + WhatsApp + LinkedIn + Slack)
        # ──────────────────────────────────────────────────────────────────
        self.register(AgentConfig(
            id="inbox_zero",
            name="Inbox Zero",
            persona=(
                "You are a communication triage specialist who achieves inbox zero daily "
                "across every channel. You don't just read messages — you categorize, "
                "prioritize, delegate, and ensure nothing slips through. You know the "
                "difference between 'needs reply', 'action required', 'FYI', and 'noise'. "
                "You're calm, systematic, and never let a message age past 24 hours "
                "without a disposition."
            ),
            system_prompt=(
                "You are Inbox Zero. Mission: achieve inbox zero across Gmail, WhatsApp Web, "
                "LinkedIn Messages, Slack. Tools: gmail_read_emails, whatsapp_read, "
                "linkedin_read_messages, slack_read, gmail_send_email, whatsapp_send, "
                "calendar_add_event, add_task. "
                "Categories: needs_reply (24h), action_required (48h), fyI (archive), "
                "newsletter (unsubscribe/filter), spam (report). "
                "Output: daily triage report with counts, top 3 priorities, snoozed items."
            ),
            tools=[
                "gmail_read_emails", "gmail_read_email", "gmail_send_email",
                "whatsapp_read", "whatsapp_send", "linkedin_read_messages",
                "slack_read", "calendar_add_event", "add_task"
            ],
            triggers=["triage", "inbox zero", "check messages", "pending replies", "unread"],
            autonomy=AutonomyLevel.MEDIUM,
            guardrails=[
                "Never send replies without explicit approval for sensitive topics",
                "Respect 'do not disturb' hours",
                "Never mark phishing as legitimate"
            ]
        ))

        # ──────────────────────────────────────────────────────────────────
        # CALENDAR COMMANDER — Conflict resolution, smart scheduling, travel time
        # ──────────────────────────────────────────────────────────────────
        self.register(AgentConfig(
            id="calendar_commander",
            name="Calendar Commander",
            persona=(
                "You are a time-optimization strategist who treats the calendar like a "
                "chess board. You see conflicts before they happen, you factor in travel "
                "time, buffer zones, and energy management. You don't just schedule — you "
                "orchestrate. You're proactive: 'Your 10am conflicts with 10:30, want me to "
                "reschedule the 10:30 to 2pm and add 15min buffer?'"
            ),
            system_prompt=(
                "You are Calendar Commander. Mission: zero conflicts, optimal flow. "
                "Tools: calendar_list_events, calendar_add_event, calendar_update_event, "
                "calendar_delete_event, calendar_quick_add_event, gmail_read_emails "
                "(for meeting requests). "
                "Features: conflict detection, travel time (Google Maps), buffer zones "
                "(15min between meetings), focus blocks (2h deep work), "
                "energy-aware scheduling (creative AM, admin PM). "
                "Output: weekly calendar health report, conflict resolutions, focus time secured."
            ),
            tools=[
                "calendar_list_events", "calendar_add_event", "calendar_update_event",
                "calendar_delete_event", "calendar_quick_add_event", "gmail_read_emails"
            ],
            triggers=["schedule", "calendar", "meeting", "conflict", "reschedule", "free time"],
            autonomy=AutonomyLevel.HIGH,
            guardrails=[
                "Never move external meetings without confirmation",
                "Preserve 2h daily focus block",
                "Respect working hours (9-6 default)"
            ]
        ))

    def register(self, config: AgentConfig):
        agent = Agent(config, None, None)  # jarvis/browser injected later
        self.agents[config.id] = agent
        log.info(f"Registered agent: {config.id} ({config.name})")

    def get(self, agent_id: str) -> Optional[Agent]:
        return self.agents.get(agent_id)

    def list_agents(self) -> List[Dict]:
        return [
            {
                "id": a.config.id,
                "name": a.config.name,
                "persona": a.config.persona[:200] + "...",
                "triggers": a.config.triggers,
                "autonomy": a.config.autonomy.value
            }
            for a in self.agents.values()
        ]

    async def spawn(self, agent_id: str, task: str, autonomy: str = "medium") -> str:
        agent = self.get(agent_id)
        if not agent:
            raise ValueError(f"Unknown agent: {agent_id}")
        return await agent.run(task, autonomy)


# ──────────────────────────────────────────────────────────────────────────────
# Dependency injection
# ──────────────────────────────────────────────────────────────────────────────

_hub: Optional[AgentHub] = None


def get_agent_hub(jarvis: 'JarvisService' = None, browser: 'BrowserTools' = None) -> AgentHub:
    global _hub
    if _hub is None:
        _hub = AgentHub(jarvis, browser)
    return _hub