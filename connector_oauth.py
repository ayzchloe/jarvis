"""OAuth2 and token support for JARVIS connectors.

Provides authorization-URL generation, code exchange, token persistence and
authenticated API calls for six connectors:

  OAuth2 flow : slack, linkedin, upwork
  Token-based : whatsapp, instagram, notion

Credentials may come from the .env file or the encrypted secrets store
(keys stored via the connector:KEY naming convention).  Tokens are kept in
plain local JSON under data/tokens (same pattern as google_tokens.json).
"""
from __future__ import annotations

import html
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
TOKEN_DIR = DATA_DIR / "tokens"
TOKEN_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")


def _secret(name: str) -> str:
    value = os.getenv(name, "")
    if value:
        return value
    from secrets_store import get_secrets
    return get_secrets().get(f"service:{name}") or ""


def _token_path(provider_id: str) -> Path:
    return TOKEN_DIR / f"{provider_id}_tokens.json"


def store_token(provider_id: str, data: dict[str, Any]) -> None:
    if "expires_in" in data and "expires_at" not in data:
        data["expires_at"] = time.time() + float(data["expires_in"])
    _token_path(provider_id).write_text(json.dumps(data), encoding="utf-8")


def get_token(provider_id: str) -> Optional[dict[str, Any]]:
    path = _token_path(provider_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def has_token(provider_id: str) -> bool:
    token = get_token(provider_id)
    return bool(token and token.get("access_token"))


def disconnect(provider_id: str) -> bool:
    path = _token_path(provider_id)
    if path.exists():
        path.unlink(missing_ok=True)
        return True
    return False


def _http_json(method: str, url: str, headers: Optional[dict[str, Any]] = None, body: Any = None, timeout: int = 20) -> dict[str, Any]:
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    request = Request(url, data=data, method=method, headers=headers or {})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
    except HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {error.code}: {raw[:500]}") from error
    except URLError as error:
        raise RuntimeError(f"Network error: {error.reason}") from error


def _client_config(provider_id: str) -> dict[str, str]:
    """Return the OAuth client credentials for a provider (env or secrets)."""
    specs = {
        "slack": ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"),
        "linkedin": ("LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET"),
        "upwork": ("UPWORK_CONSUMER_KEY", "UPWORK_CONSUMER_SECRET"),
    }
    keys = specs.get(provider_id, ())
    creds = {key: _secret(key) for key in keys}
    return creds


def _token_specs(provider_id: str) -> list[str]:
    return {
        "slack": ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"),
        "linkedin": ("LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET"),
        "upwork": ("UPWORK_CONSUMER_KEY", "UPWORK_CONSUMER_SECRET"),
        "whatsapp": ("WHATSAPP_TOKEN", "WHATSAPP_ACCOUNT_ID"),
        "instagram": ("INSTAGRAM_TOKEN", "INSTAGRAM_BUSINESS_ID"),
        "notion": ("NOTION_TOKEN",),
    }.get(provider_id, ())


AUTHURIS = {
    "slack": "https://slack.com/oauth/v2/authorize",
    "linkedin": "https://www.linkedin.com/oauth/v2/authorization",
    "upwork": "https://www.upwork.com/ab/account-security/oauth2/authorize",
}
TOKENURIS = {
    "slack": "https://slack.com/api/oauth.v2.access",
    "linkedin": "https://www.linkedin.com/oauth/v2/accessToken",
    "upwork": "https://www.upwork.com/ab/account-security/oauth2/token",
}
SCOPES = {
    "slack": "chat:write,channels:read,channels:history,users:read",
    "linkedin": "openid profile email w_member_social",
    "upwork": "jobs_read hr_read",
}
OAUTH_PROVIDERS = {"slack", "linkedin", "upwork"}


def default_redirect_uri(provider_id: str) -> str:
    return f"http://127.0.0.1:8100/auth/{provider_id}/callback"


def get_authorization_url(provider_id: str, redirect_uri: Optional[str] = None) -> str:
    """Build a provider authorization URL. Raises RuntimeError if client creds are missing."""
    if provider_id not in OAUTH_PROVIDERS:
        raise RuntimeError(f"{provider_id} does not use an OAuth browser flow. Store its token via the chat (store_secret).")
    app_id = "SLACK" if provider_id == "slack" else ("LINKEDIN" if provider_id == "linkedin" else "UPWORK")
    client_id = _secret(f"{app_id}_CLIENT_ID")
    if not client_id:
        raise RuntimeError(f"Missing {app_id}_CLIENT_ID. Add it to .env or store it via the chat: store_secret {provider_id} {app_id}_CLIENT_ID …")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri or default_redirect_uri(provider_id),
        "response_type": "code",
        "state": f"jarvis_{provider_id}",
        "scope": SCOPES.get(provider_id, ""),
    }
    if provider_id == "upwork":
        params["domain"] = "https://www.upwork.com"
    return f"{AUTHURIS[provider_id]}?{urlencode(params)}"


def exchange_code(provider_id: str, code: str, redirect_uri: Optional[str] = None) -> dict[str, Any]:
    """Exchange an authorization code for tokens and persist them."""
    app_id = "SLACK" if provider_id == "slack" else ("LINKEDIN" if provider_id == "linkedin" else "UPWORK")
    client_id = _secret(f"{app_id}_CLIENT_ID")
    client_secret = _secret(f"{app_id}_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(f"Missing {app_id}_CLIENT_ID or {app_id}_CLIENT_SECRET.")
    body = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri or default_redirect_uri(provider_id),
        "grant_type": "authorization_code",
    }
    if provider_id == "slack":
        body.pop("code", None)
        body["code"] = code
    token_data = _http_json("POST", TOKENURIS[provider_id], {"Content-Type": "application/x-www-form-urlencoded"}, urlencode(body))
    if "error" in token_data:
        raise RuntimeError(f"Token exchange failed: {token_data.get('error_description') or token_data['error']}")
    if provider_id == "slack":
        token_data["access_token"] = token_data.get("access_token") or token_data.get("authed_user", {}).get("access_token", "")
        token_data["team_name"] = token_data.get("team", {}).get("name", "")
    elif provider_id == "linkedin":
        token_data.setdefault("account", "LinkedIn")
    token_data["connected_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    store_token(provider_id, token_data)
    return token_data


def _bearer_headers(provider_id: str, extras: Optional[dict[str, Any]] = None) -> dict[str, str]:
    token = get_token(provider_id)
    if not token or not token.get("access_token"):
        raise RuntimeError(f"{provider_id} is not connected. Open the Connectors panel on the dashboard and click CONNECT.")
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    headers.update(extras or {})
    return headers


def api(provider_id: str, method: str, url: str, body: Any = None) -> Any:
    """Authenticated request against a provider using its stored token.
    Returns the parsed JSON. Panels use token access directly (WhatsApp/Instagram/Notion)."""
    if provider_id in ("whatsapp", "instagram"):
        token = get_token(provider_id) or {}
        access = token.get("access_token") or _secret("WHATSAPP_TOKEN") or _secret("INSTAGRAM_TOKEN")
        if not access:
            raise RuntimeError(f"{provider_id} is not connected. Store its token via the chat: store_secret …")
        url = f"{url}{'&' if '?' in url else '?'}access_token={access}"
        headers = {"Content-Type": "application/json"}
    elif provider_id == "notion":
        token = get_token(provider_id) or {}
        access = token.get("access_token") or _secret("NOTION_TOKEN")
        if not access:
            raise RuntimeError("notion is not connected. Store its token (NOTION_TOKEN) via store_secret ….")
        headers = {"Authorization": f"Bearer {access}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"}
    else:
        headers = _bearer_headers(provider_id)
    return _http_json(method, url, headers, body)


def status(provider_id: str) -> dict[str, Any]:
    """Return the connection status for one connector."""
    name = {
        "slack": "Slack", "linkedin": "LinkedIn", "upwork": "Upwork",
        "whatsapp": "WhatsApp", "instagram": "Instagram", "notion": "Notion",
    }[provider_id]
    summary = {
        "slack": "Read and send team messages",
        "linkedin": "Post, read messages, and search people",
        "upwork": "Search jobs and submit proposals",
        "whatsapp": "WhatsApp Cloud API messaging",
        "instagram": "Read DMs and profile engagement",
        "notion": "Query and update Notion databases",
    }[provider_id]
    base = {"id": provider_id, "name": name, "summary": summary,
            "required_credentials": list(_token_specs(provider_id))}
    if has_token(provider_id):
        token = get_token(provider_id) or {}
        account = (token.get("team_name") or token.get("email")
                   or token.get("account") or "Connected")
        return {**base, "status": "connected", "account": account,
                "account_email": account, "connected_at": token.get("connected_at", "")}
    if any(_secret(key) for key in _token_specs(provider_id)):
        if provider_id in OAUTH_PROVIDERS:
            return {**base, "status": "ready_to_authorize"}
        return {**base, "status": "connected", "account": "Token stored"}
    return {**base, "status": "credentials_required"}


def all_status() -> list[dict[str, Any]]:
    return [status(p) for p in ("slack", "linkedin", "upwork", "whatsapp", "instagram", "notion")]


def success_page(provider_id: str, account: str = "your account") -> str:
    safe = html.escape(provider_id)
    safe_account = html.escape(account)
    safe_state = account.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>JARVIS · {safe} Connected</title>
<style>
body{{background:#03060d;color:#eaf8ff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.card{{background:#091725;border:1px solid #54ddff44;border-radius:16px;padding:36px 40px;text-align:center;max-width:440px;box-shadow:0 0 40px #2adaff33}}
h2{{color:#83ffd2;margin:0 0 10px;font-size:22px}}p{{color:#80a5b9;font-size:14px;line-height:1.5;margin:0 0 20px}}
.badge{{display:inline-block;background:#153247;color:#54ddff;padding:6px 14px;border-radius:20px;font-size:13px;margin-bottom:24px}}
button{{background:#54ddff;color:#04202d;border:0;border-radius:8px;padding:12px 24px;font-weight:700;cursor:pointer}}
</style></head><body><div class="card"><h2>✓ {safe} Connected</h2>
<p>JARVIS is now linked to {safe}.</p><div class="badge">{safe_account}</div><br>
<button onclick="window.close()">Back to Dashboard</button></div>
<script>
if (window.opener) {{
  window.opener.postMessage({{ type: 'jarvis_connector_connected', provider: '{safe}', account: '{safe_state}' }}, '*');
  setTimeout(() => window.close(), 1800);
}}
</script></body></html>"""


def test_connection(provider_id: str) -> dict[str, Any]:
    """Fire a cheap read call to verify the stored credentials actually work."""
    checks = {
        "slack": ("GET", "https://slack.com/api/auth.test", "Slack"),
        "whatsapp": ("GET", "https://graph.facebook.com/v19.0/{acct}", "WhatsApp"),
        "instagram": ("GET", "https://graph.facebook.com/v19.0/{acct}", "Instagram"),
        "notion": ("GET", "https://api.notion.com/v1/users/me", "Notion"),
        "linkedin": ("GET", "https://api.linkedin.com/v2/userinfo", "LinkedIn"),
    }
    if provider_id not in checks:
        return {"provider": provider_id, "ok": None, "message": "No automatic test available."}
    method, url, label = checks[provider_id]
    if provider_id in ("whatsapp", "instagram"):
        token = get_token(provider_id) or {}
        acct = token.get("acct", "") or _secret("WHATSAPP_ACCOUNT_ID") or _secret("INSTAGRAM_BUSINESS_ID")
        if not acct:
            return {"provider": provider_id, "ok": False, "message": f"{label} token stored but account ID is missing."}
        url = url.format(acct=acct)
    try:
        data = api(provider_id, method, url)
        if provider_id == "slack" and data.get("ok") is False:
            return {"provider": provider_id, "ok": False, "message": data.get("error", "Slack check failed.")}
        return {"provider": provider_id, "ok": True, "message": f"{label} connection verified.", "account": str(data.get("user") or data.get("username") or data.get("name") or "")}
    except Exception as error:
        return {"provider": provider_id, "ok": False, "message": str(error)}