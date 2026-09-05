from dotenv import load_dotenv
load_dotenv()  # MUST be called BEFORE importing auth

from typing import Any, Dict, List
import html
import logging
import os
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ai_service import AssistantUnavailableError, JarvisService
from auth import connector_status
from agent_hub import AgentConfig, AutonomyLevel

logger = logging.getLogger(__name__)

app = FastAPI(title="JARVIS Local Control API", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:9999",
        "http://localhost:9999",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Jarvis-Session"],
)

assistant_service = JarvisService()


class CommandIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ConfirmationIn(BaseModel):
    id: str
    approved: bool


class SpeechIn(BaseModel):
    text: str = Field(min_length=1, max_length=1200)


def session_id(value: str | None) -> str:
    return value if value and len(value) <= 128 else "default"


@app.get("/health")
def health() -> dict[str, Any]:
    return assistant_service.health()


@app.get("/stats")
def stats() -> dict[str, Any]:
    return assistant_service.system_info()


@app.get("/connectors")
def connectors() -> dict[str, Any]:
    return {"connectors": connector_status()}


@app.get("/memory")
def memory(query: str = "", limit: int = 10) -> dict[str, Any]:
    return assistant_service.memories(query, limit)


@app.get("/tasks")
def tasks(limit: int = 10) -> dict[str, Any]:
    return assistant_service.tasks(limit)


@app.get("/skills")
def skills() -> dict[str, Any]:
    return {"skills": assistant_service.skills.all()}


class SkillToggleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool


@app.post("/skills/toggle")
def skills_toggle(body: SkillToggleIn) -> dict[str, Any]:
    skill = assistant_service.skills.set_enabled(body.name, body.enabled)
    if not skill:
        raise HTTPException(404, "Skill not found.")
    return {"name": skill["name"], "enabled": skill["enabled"]}


@app.post("/command")
def command(body: CommandIn, x_jarvis_session: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        return assistant_service.execute(body.text.strip(), session_id(x_jarvis_session))
    except AssistantUnavailableError as error:
        raise HTTPException(503, str(error)) from error

 
@app.post("/actions/confirm")
def confirm_action(body: ConfirmationIn, x_jarvis_session: str | None = Header(default=None)) -> dict[str, str]:
    try:
        return assistant_service.confirm(body.id, body.approved, session_id(x_jarvis_session))
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/speak")
def speak(body: SpeechIn) -> dict[str, Any]:
    return assistant_service.speak_aloud(body.text)


# ──────────────────────────────────────────────────────────────────────────────
# Agent Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/agents")
def list_agents() -> dict[str, Any]:
    """List all available agents."""
    return {"agents": assistant_service.list_agents()}


class SpawnAgentIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    task: str = Field(min_length=1, max_length=4000)
    autonomy: str = Field(default="medium", pattern="^(low|medium|high)$")


@app.post("/agents/spawn")
def spawn_agent(body: SpawnAgentIn) -> dict[str, Any]:
    """Spawn an agent to execute a task."""
    return assistant_service.spawn_agent(body.agent_id, body.task, body.autonomy)


@app.post("/agents/run")
def spawn_agent_async(body: SpawnAgentIn) -> dict[str, Any]:
    """Launch an agent in the background; returns a run_id for live polling."""
    return assistant_service.spawn_agent_async(body.agent_id, body.task, body.autonomy)


@app.get("/agents/{agent_id}")
def agent_status(agent_id: str) -> dict[str, Any]:
    """Get status and run history for a specific agent."""
    return assistant_service.agent_status(agent_id)


class CreateSkillIn(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern="^[a-z_]+$")
    description: str = Field(min_length=1, max_length=500)
    triggers: List[str] = Field(min_length=1, max_length=10)
    prompt: str = Field(default="", max_length=4000)
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    script: str = Field(default="", max_length=200)


@app.post("/skills/create")
def create_skill(body: CreateSkillIn) -> dict[str, Any]:
    """Create a new skill."""
    return assistant_service.create_skill(
        body.name, body.description, body.triggers,
        body.prompt, body.steps, body.script
    )


# ──────────────────────────────────────────────────────────────────────────────
# Google OAuth & Model Endpoints
# ──────────────────────────────────────────────────────────────────────────────

from fastapi.responses import HTMLResponse
from auth import google_workspace

@app.get("/auth/google/url")
def auth_google_url(redirect_uri: str | None = None) -> dict[str, Any]:
    try:
        url = google_workspace.get_authorization_url(redirect_uri)
        return {"url": url}
    except Exception as error:
        raise HTTPException(400, str(error)) from error


@app.get("/auth/google/callback", response_class=HTMLResponse)
def auth_google_callback(code: str = "", error: str = "", redirect_uri: str | None = None) -> HTMLResponse:
    if error:
        logger.warning(f"Google OAuth error: {error}")
        return HTMLResponse(
            content=f"""<!DOCTYPE html><html><head><title>Authentication Failed</title>
            <style>body{{font-family:sans-serif;background:#060a10;color:#ff6577;text-align:center;padding:50px;}}</style></head>
            <body><h2>Authentication Cancelled or Failed</h2><p>{html.escape(error)}</p><button onclick="window.close()">Close Window</button></body></html>""",
            status_code=400,
        )
    if not code:
        logger.warning("Google OAuth callback missing authorization code")
        return HTMLResponse(
            content="""<!DOCTYPE html><html><body><h2>Missing authorization code.</h2></body></html>""",
            status_code=400,
        )
    try:
        token_data = google_workspace.exchange_code(code, redirect_uri)
        email = html.escape(token_data.get("email", "your Google account"))
        safe_email_js = token_data.get("email", "").replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>JARVIS · Google Workspace Connected</title>
    <style>
        body {{
            background: #03060d;
            color: #eaf8ff;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            margin: 0;
        }}
        .card {{
            background: #091725;
            border: 1px solid #54ddff44;
            border-radius: 16px;
            padding: 36px 40px;
            text-align: center;
            max-width: 440px;
            box-shadow: 0 0 40px #2adaff33;
        }}
        h2 {{ color: #83ffd2; margin: 0 0 10px; font-size: 22px; }}
        p {{ color: #80a5b9; font-size: 14px; line-height: 1.5; margin: 0 0 20px; }}
        .badge {{ display: inline-block; background: #153247; color: #54ddff; padding: 6px 14px; border-radius: 20px; font-size: 13px; margin-bottom: 24px; }}
        button {{
            background: #54ddff;
            color: #04202d;
            border: 0;
            border-radius: 8px;
            padding: 12px 24px;
            font-weight: 700;
            cursor: pointer;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h2>✓ Google Workspace Connected</h2>
        <p>JARVIS is now linked to your Google account for Gmail, Calendar, Drive, Docs, Sheets, Tasks, and Contacts.</p>
        <div class="badge">{email}</div><br>
        <button onclick="window.close()">Back to Dashboard</button>
    </div>
    <script>
        if (window.opener) {{
            window.opener.postMessage({{ type: 'jarvis_google_connected', email: '{safe_email_js}' }}, '*');
            setTimeout(() => window.close(), 1800);
        }}
    </script>
</body>
</html>"""
        return HTMLResponse(content=html_content)
    except Exception as exc:
        logger.error(f"Google OAuth callback error: {exc}")
        return HTMLResponse(
            content=f"""<!DOCTYPE html><html><body style="background:#05090f;color:#ff6b7a;padding:40px;font-family:sans-serif;">
            <h2>Authorization Error</h2><p>{html.escape(str(exc))}</p><button onclick="window.close()">Close</button></body></html>""",
            status_code=500,
        )


@app.get("/auth/google/status")
def auth_google_status() -> dict[str, Any]:
    return google_workspace.status()


@app.post("/auth/google/disconnect")
def auth_google_disconnect() -> dict[str, Any]:
    disconnected = google_workspace.disconnect()
    return {"disconnected": disconnected}


# Model selection endpoints
class ModelConfigIn(BaseModel):
    provider: str
    model: str


@app.post("/model/set")
def model_set(body: ModelConfigIn) -> dict[str, Any]:
    assistant_service.set_model(body.provider, body.model)
    return {"provider": body.provider, "model": body.model, "status": "ok"}


@app.get("/model/get")
def model_get() -> dict[str, Any]:
    return {"provider": assistant_service.get_provider(), "model": assistant_service.get_model()}


@app.get("/models")
def model_list() -> dict[str, Any]:
    return assistant_service.list_models()


# ──────────────────────────────────────────────────────────────────────────────
# Connector OAuth flow (Slack / LinkedIn / Upwork) + token connectors
# ──────────────────────────────────────────────────────────────────────────────

from connector_oauth import (
    default_redirect_uri,
    disconnect as connector_disconnect,
    exchange_code as connector_exchange_code,
    get_authorization_url,
    status as connector_oauth_status,
    success_page,
    test_connection,
)
from connector_oauth import OAUTH_PROVIDERS as CONNECTOR_OAUTH_PROVIDERS

ALL_CONNECTOR_IDS = {"slack", "linkedin", "upwork", "whatsapp", "instagram", "notion"}


@app.get("/auth/{provider}/url")
def auth_connector_url(provider: str, redirect_uri: str | None = None) -> dict[str, Any]:
    if provider not in ALL_CONNECTOR_IDS:
        raise HTTPException(404, f"Unknown connector '{provider}'.")
    try:
        return {"provider": provider, "url": get_authorization_url(provider, redirect_uri)}
    except Exception as error:
        raise HTTPException(400, str(error)) from error


@app.get("/auth/{provider}/callback", response_class=HTMLResponse)
def auth_connector_callback(
    provider: str,
    code: str = "",
    error: str = "",
    redirect_uri: str | None = None,
) -> HTMLResponse:
    if provider not in ALL_CONNECTOR_IDS:
        return HTMLResponse(content="<h2>Unknown connector.</h2>", status_code=404)
    if error:
        return HTMLResponse(
            content=f"""<!DOCTYPE html><html><head><title>Authentication Failed</title>
            <style>body{{font-family:sans-serif;background:#060a10;color:#ff6577;text-align:center;padding:50px;}}</style></head>
            <body><h2>Authentication Cancelled or Failed</h2><p>{html.escape(error)}</p>
            <button onclick="window.close()">Close Window</button></body></html>""",
            status_code=400,
        )
    if not code:
        return HTMLResponse(content="""<!DOCTYPE html><html><body><h2>Missing authorization code.</h2></body></html>""", status_code=400)
    try:
        token_data = connector_exchange_code(provider, code, redirect_uri)
        account = (token_data.get("team_name") or token_data.get("email")
                   or token_data.get("account") or "your account")
        return HTMLResponse(content=success_page(provider, account))
    except Exception as exc:
        logger.error(f"Connector {provider} callback error: {exc}")
        return HTMLResponse(
            content=f"""<!DOCTYPE html><html><body style="background:#05090f;color:#ff6b7a;padding:40px;font-family:sans-serif;">
            <h2>Authorization Error</h2><p>{html.escape(str(exc))}</p><button onclick="window.close()">Close</button></body></html>""",
            status_code=500,
        )


@app.get("/auth/{provider}/status")
def auth_connector_status(provider: str) -> dict[str, Any]:
    if provider not in ALL_CONNECTOR_IDS:
        raise HTTPException(404, f"Unknown connector '{provider}'.")
    return connector_oauth_status(provider)


@app.post("/auth/{provider}/disconnect")
def auth_connector_disconnect(provider: str) -> dict[str, Any]:
    if provider not in ALL_CONNECTOR_IDS:
        raise HTTPException(404, f"Unknown connector '{provider}'.")
    return {"provider": provider, "disconnected": connector_disconnect(provider)}


@app.get("/connectors/test/{provider}")
def connectors_test(provider: str) -> dict[str, Any]:
    if provider not in ALL_CONNECTOR_IDS:
        raise HTTPException(404, f"Unknown connector '{provider}'.")
    return test_connection(provider)


if __name__ == "__main__":
    import uvicorn

    print("JARVIS backend running at http://127.0.0.1:8100")
    uvicorn.run(app, host="127.0.0.1", port=8100)
