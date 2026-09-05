"""Local connector credential requirements and Google Workspace integration for JARVIS.

This module reports configuration state and provides full OAuth2 flow, token
persistence, auto-refresh, and Gmail + Google Calendar tools.  OAuth tokens and
secrets remain in the local environment and are never returned to the browser
or language model.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
import json
import mimetypes
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOKENS_PATH = DATA_DIR / "google_tokens.json"

load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Connector registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConnectorSpec:
    id: str
    name: str
    summary: str
    credentials: tuple[str, ...]
    portal_url: str


CONNECTORS = (
    ConnectorSpec(
        "google",
        "Google Workspace",
        "Gmail, Calendar, Drive, Docs, and Contacts",
        ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"),
        "https://console.cloud.google.com/apis/credentials",
    ),
    ConnectorSpec(
        "microsoft",
        "Microsoft 365",
        "Outlook, Calendar, OneDrive, Teams, and SharePoint",
        ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_TENANT_ID", "MICROSOFT_REDIRECT_URI"),
        "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
    ),
    ConnectorSpec(
        "slack",
        "Slack",
        "Search and send team messages",
        ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET", "SLACK_REDIRECT_URI"),
        "https://api.slack.com/apps",
    ),
    ConnectorSpec(
        "github",
        "GitHub",
        "Repositories, issues, pull requests, and workflows",
        ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GITHUB_REDIRECT_URI"),
        "https://github.com/settings/developers",
    ),
    ConnectorSpec(
        "jira",
        "Atlassian Jira",
        "Projects, tickets, sprints, and issue updates",
        ("JIRA_CLIENT_ID", "JIRA_CLIENT_SECRET", "JIRA_REDIRECT_URI", "JIRA_CLOUD_ID"),
        "https://developer.atlassian.com/console/myapps/",
    ),
)


def connector_status() -> list[dict[str, object]]:
    g_status = google_workspace.status()

    results = []
    for connector in CONNECTORS:
        if connector.id == "google":
            if g_status.get("connected"):
                st = "connected"
            elif g_status.get("configured") or all(os.getenv(key) for key in connector.credentials):
                st = "ready_to_authorize"
            else:
                st = "credentials_required"

            results.append({
                "id": connector.id,
                "name": connector.name,
                "summary": connector.summary,
                "status": st,
                "connected": g_status.get("connected", False),
                "account_email": g_status.get("email", ""),
                "account_name": g_status.get("name", ""),
                "required_credentials": list(connector.credentials),
                "portal_url": connector.portal_url,
            })
        else:
            is_ready = all(os.getenv(key) for key in connector.credentials)
            results.append({
                "id": connector.id,
                "name": connector.name,
                "summary": connector.summary,
                "status": "ready_to_authorize" if is_ready else "credentials_required",
                "connected": False,
                "account_email": "",
                "account_name": "",
                "required_credentials": list(connector.credentials),
                "portal_url": connector.portal_url,
            })
    return results


# ---------------------------------------------------------------------------
# Google Workspace connector (OAuth2 + Gmail + Calendar)
# ---------------------------------------------------------------------------

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URI = "https://www.googleapis.com/oauth2/v2/userinfo"

SCOPES = [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/contacts",
]


class GoogleWorkspaceConnector:
    def __init__(self) -> None:
        self.reload_config()
        self._email_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cache_ttl = 30  # seconds
        self._session: Any = None  # persistent keep-alive HTTP session

    def _http_session(self) -> Any:
        """Reuse one HTTPS connection pool. Fresh connections were measured at
        1-40s each due to TLS/DNS overhead; keep-alive drops them to ~0.4s."""
        if self._session is None:
            import requests
            self._session = requests.Session()
        return self._session

    def reload_config(self) -> None:
        load_dotenv(BASE_DIR / ".env", override=True)
        self.client_id = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
        self.client_secret = (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
        self.redirect_uri = (
            os.getenv("GOOGLE_REDIRECT_URI")
            or "http://127.0.0.1:8100/auth/google/callback"
        ).strip()

    def has_credentials(self) -> bool:
        self.reload_config()
        return bool(self.client_id and self.client_secret)

    def get_tokens(self) -> dict[str, Any] | None:
        if not TOKENS_PATH.is_file():
            return None
        try:
            return json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def save_tokens(self, data: dict[str, Any]) -> None:
        TOKENS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def disconnect(self) -> bool:
        if TOKENS_PATH.is_file():
            try:
                TOKENS_PATH.unlink()
                return True
            except OSError:
                return False
        return True

    def get_authorization_url(self, redirect_uri: str | None = None) -> str:
        self.reload_config()
        if not self.has_credentials():
            raise RuntimeError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be configured in .env")

        uri = redirect_uri or self.redirect_uri
        params = {
            "client_id": self.client_id,
            "redirect_uri": uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
        return f"{GOOGLE_AUTH_URI}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str | None = None) -> dict[str, Any]:
        self.reload_config()
        uri = redirect_uri or self.redirect_uri
        payload = urlencode({
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": uri,
            "grant_type": "authorization_code",
        }).encode("utf-8")

        req = Request(
            GOOGLE_TOKEN_URI,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=15) as res:
                token_data = json.loads(res.read().decode("utf-8"))
        except HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"Token exchange failed ({e.code}): {err_body}")
            raise RuntimeError(f"Token exchange failed ({e.code}): {err_body}. Please check your Google Cloud Console configuration (redirect URI, OAuth consent screen).") from e
        except URLError as e:
            logger.error(f"Network error during token exchange: {e.reason}")
            raise RuntimeError(f"Network error during token exchange: {e.reason}") from e

        expires_in = token_data.get("expires_in", 3600)
        token_data["expires_at"] = time.time() + expires_in

        # Fetch profile information
        access_token = token_data.get("access_token")
        profile = self._fetch_user_profile(access_token) if access_token else {}
        token_data["email"] = profile.get("email", "")
        token_data["name"] = profile.get("name", "")
        token_data["picture"] = profile.get("picture", "")
        token_data["connected_at"] = datetime.now(timezone.utc).isoformat()

        # If refresh token not returned (because user re-authorized), retain existing one if present
        if "refresh_token" not in token_data:
            existing = self.get_tokens()
            if existing and existing.get("refresh_token"):
                token_data["refresh_token"] = existing["refresh_token"]

        self.save_tokens(token_data)
        logger.info(f"Successfully connected Google account: {token_data.get('email', 'unknown')}")
        return token_data

    def _fetch_user_profile(self, access_token: str) -> dict[str, Any]:
        try:
            req = Request(
                GOOGLE_USERINFO_URI,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            with urlopen(req, timeout=10) as res:
                return json.loads(res.read().decode("utf-8"))
        except Exception:
            return {}

    def get_valid_access_token(self) -> str:
        tokens = self.get_tokens()
        if not tokens:
            raise RuntimeError("Google Workspace is not connected. Please connect your Google account in the dashboard.")

        # Check expiration with 60 second buffer
        expires_at = tokens.get("expires_at", 0)
        if time.time() < (expires_at - 60) and tokens.get("access_token"):
            return tokens["access_token"]

        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            # Fallback to existing access_token if no refresh token
            if tokens.get("access_token"):
                return tokens["access_token"]
            raise RuntimeError("Google session expired. Please re-authenticate your Google account.")

        # Refresh the token
        self.reload_config()
        payload = urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }).encode("utf-8")

        req = Request(
            GOOGLE_TOKEN_URI,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=15) as res:
                refreshed = json.loads(res.read().decode("utf-8"))
        except HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"Google token refresh failed ({e.code}): {err_body}")
            raise RuntimeError(f"Google token refresh failed ({e.code}): {err_body}. Please reconnect your account.") from e
        except URLError as e:
            logger.error(f"Network error during token refresh: {e.reason}")
            raise RuntimeError(f"Network error during token refresh: {e.reason}") from e

        tokens["access_token"] = refreshed["access_token"]
        tokens["expires_at"] = time.time() + refreshed.get("expires_in", 3600)
        if "refresh_token" in refreshed:
            tokens["refresh_token"] = refreshed["refresh_token"]

        self.save_tokens(tokens)
        return tokens["access_token"]

    def is_connected(self) -> bool:
        tokens = self.get_tokens()
        return bool(tokens and (tokens.get("access_token") or tokens.get("refresh_token")))

    def status(self) -> dict[str, Any]:
        self.reload_config()
        configured = self.has_credentials()
        tokens = self.get_tokens()
        connected = bool(tokens and (tokens.get("access_token") or tokens.get("refresh_token")))

        return {
            "configured": configured,
            "connected": connected,
            "email": tokens.get("email", "") if connected and tokens else "",
            "name": tokens.get("name", "") if connected and tokens else "",
            "picture": tokens.get("picture", "") if connected and tokens else "",
            "connected_at": tokens.get("connected_at", "") if connected and tokens else "",
            "scopes": tokens.get("scope", "").split() if connected and tokens else [],
            "client_id_set": bool(self.client_id),
            "client_secret_set": bool(self.client_secret),
            "redirect_uri": self.redirect_uri,
        }

    # -----------------------------------------------------------------------
    # GMAIL TOOLS
    # -----------------------------------------------------------------------

    def _api_request(
        self,
        url: str,
        method: str = "GET",
        json_data: dict[str, Any] | None = None,
        raw_data: bytes | None = None,
        content_type: str = "application/json",
        timeout: int = 30,
    ) -> dict[str, Any]:
        import requests
        import time as _time
        token = self.get_valid_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        data = None
        if json_data is not None:
            data = json.dumps(json_data)
            headers["Content-Type"] = "application/json"
        elif raw_data is not None:
            data = raw_data
            headers["Content-Type"] = content_type

        # This machine's TLS handshakes to Google spike unpredictably (1-41s
        # measured). Retry idempotent verbs once; NEVER retry POST/PUT so a
        # sent email can't be duplicated by a flaky timeout.
        retriable = method.upper() in {"GET", "DELETE", "PATCH"}
        attempts = 2 if retriable else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                resp = self._http_session().request(
                    method, url, headers=headers, data=data, timeout=(5, timeout)
                )
                if resp.status_code >= 400:
                    err_body = resp.text[:400]
                    hint = ""
                    if resp.status_code in (401, 403):
                        hint = " If this feature was just added, disconnect and reconnect your Google account in the dashboard to grant the new permissions."
                    if "has not been used in project" in resp.text or "it is disabled" in resp.text:
                        import re as _re
                        match = _re.search(r"apis(?:/api)?/([a-z0-9.-]+googleapis\.com)", err_body)
                        api_name = match.group(1) if match else ""
                        project = self.client_id.split("@")[0].split("-")[0] if self.client_id else ""
                        enable_url = f"https://console.developers.google.com/apis/api/{api_name}?project={project}" if api_name else "https://console.developers.google.com/apis/library"
                        raise RuntimeError(
                            f"Google API Error ({resp.status_code}): {err_body}."
                            f" This Google API is not enabled on your Cloud project yet."
                            f" Open {enable_url} and click Enable, then ask me again."
                        )
                    if "insufficient authentication scopes" in resp.text.casefold() or "insufficient permission" in resp.text.casefold():
                        raise RuntimeError(
                            f"Google API Error ({resp.status_code}): {err_body}."
                            f" Your saved Google session is missing a permission for this action."
                            f" Disconnect and reconnect your Google account in the dashboard to grant it.{hint}"
                        )
                    raise RuntimeError(f"Google API Error ({resp.status_code}): {err_body}.{hint}")
                return resp.json() if resp.text.strip() else {"status": "ok"}
            except requests.exceptions.Timeout as error:
                last_error = error
                if attempt + 1 < attempts:
                    _time.sleep(0.6)
                    continue
            except requests.exceptions.RequestException as error:
                last_error = error
                # A dead pooled connection can surface here; rebuild and retry.
                self._session = None
                if attempt + 1 < attempts:
                    continue
        detail = str(last_error) if last_error else "unknown"
        raise RuntimeError(f"Google request failed after retry ({method}): {detail}")

    def list_emails(self, query: str = "is:inbox", max_results: int = 5) -> dict[str, Any]:
        """List emails matching a query (default recent inbox messages)."""
        import time
        # Check cache
        cache_key = f"{query}:{max_results}"
        now = time.time()
        if cache_key in self._email_cache:
            cached_time, cached_result = self._email_cache[cache_key]
            if now - cached_time < self._cache_ttl:
                return cached_result

        limit = max(1, min(max_results, 50))
        params = {"maxResults": limit}
        if query:
            params["q"] = query

        url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{urlencode(params)}"
        data = self._api_request(url)
        messages_meta = data.get("messages", [])
        if not messages_meta:
            return {"count": 0, "emails": [], "summary": f"No emails found for query '{query}'."}

        emails = []
        meta_urls = [
            (
                meta["id"],
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{meta['id']}"
                "?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date",
            )
            for meta in messages_meta
        ]

        def _fetch_meta(item: tuple[str, str]) -> dict[str, Any]:
            msg_id, url_i = item
            msg = self._api_request(url_i)
            payload = msg.get("payload", {})
            headers_dict = {h["name"].casefold(): h["value"] for h in payload.get("headers", [])}
            return {
                "id": msg_id,
                "threadId": msg.get("threadId", ""),
                "subject": headers_dict.get("subject", "(No Subject)"),
                "from": headers_dict.get("from", "Unknown"),
                "date": headers_dict.get("date", ""),
                "snippet": msg.get("snippet", ""),
            }

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(meta_urls))) as pool:
            emails = list(pool.map(_fetch_meta, meta_urls))

        summary_lines = [
            f"- From: {e['from']} | Subject: {e['subject']} ({e['snippet'][:80]}...)"
            for e in emails
        ]
        result = {
            "count": len(emails),
            "query": query,
            "emails": emails,
            "summary": f"Found {len(emails)} emails:\n" + "\n".join(summary_lines),
        }
        # Cache result
        self._email_cache[cache_key] = (time.time(), result)
        return result

    def read_email(self, message_id: str) -> dict[str, Any]:
        """Fetch full email content by message ID."""
        url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}?format=full"
        msg = self._api_request(url)

        payload = msg.get("payload", {})
        headers_list = payload.get("headers", [])
        headers = {h["name"].casefold(): h["value"] for h in headers_list}

        body_text = ""
        # Extract body text from parts
        parts = payload.get("parts", [])
        if not parts and payload.get("body", {}).get("data"):
            raw_body = payload["body"]["data"]
            body_text = base64.urlsafe_b64decode(raw_body.encode("ascii")).decode("utf-8", errors="replace")
        else:
            for part in parts:
                mime_type = part.get("mimeType", "")
                if mime_type == "text/plain" and part.get("body", {}).get("data"):
                    raw_body = part["body"]["data"]
                    body_text = base64.urlsafe_b64decode(raw_body.encode("ascii")).decode("utf-8", errors="replace")
                    break
            if not body_text and parts:
                for part in parts:
                    if part.get("body", {}).get("data"):
                        raw_body = part["body"]["data"]
                        body_text = base64.urlsafe_b64decode(raw_body.encode("ascii")).decode("utf-8", errors="replace")
                        break

        return {
            "id": message_id,
            "subject": headers.get("subject", "(No Subject)"),
            "from": headers.get("from", "Unknown"),
            "to": headers.get("to", ""),
            "date": headers.get("date", ""),
            "snippet": msg.get("snippet", ""),
            "body": body_text[:4000],
        }

    def draft_email(self, to: str, subject: str, body: str) -> dict[str, Any]:
        """Create an email draft in Gmail."""
        message = MIMEText(body)
        message["to"] = to.strip()
        message["subject"] = subject.strip()
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

        url = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
        result = self._api_request(url, method="POST", json_data={"message": {"raw": raw}})
        draft_id = result.get("id", "")
        return {
            "status": "draft_created",
            "draft_id": draft_id,
            "to": to,
            "subject": subject,
            "message": f"Draft created in Gmail for {to} with subject '{subject}'.",
        }

    def send_email(self, to: str, subject: str, body: str) -> dict[str, Any]:
        """Send an email directly through Gmail."""
        message = MIMEText(body)
        message["to"] = to.strip()
        message["subject"] = subject.strip()
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

        url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
        result = self._api_request(url, method="POST", json_data={"raw": raw})
        msg_id = result.get("id", "")
        return {
            "status": "sent",
            "message_id": msg_id,
            "to": to,
            "subject": subject,
            "message": f"Email successfully sent to {to} with subject '{subject}'.",
        }

    # -----------------------------------------------------------------------
    # GOOGLE CALENDAR TOOLS
    # -----------------------------------------------------------------------

    def list_calendar_events(
        self,
        time_min: str | None = None,
        time_max: str | None = None,
        days_ahead: int = 7,
        max_results: int = 10,
    ) -> dict[str, Any]:
        """List upcoming Google Calendar events."""
        now = datetime.now(timezone.utc)
        if not time_min:
            t_min = now.isoformat()
        else:
            t_min = time_min

        if not time_max:
            t_max = (now + timedelta(days=max(1, days_ahead))).isoformat()
        else:
            t_max = time_max

        params = {
            "timeMin": t_min,
            "timeMax": t_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max(1, min(max_results, 50)),
        }
        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events?{urlencode(params)}"
        data = self._api_request(url)
        items = data.get("items", [])

        events = []
        for item in items:
            start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
            end = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
            events.append({
                "id": item.get("id"),
                "summary": item.get("summary", "(Untitled Event)"),
                "description": item.get("description", ""),
                "location": item.get("location", ""),
                "start": start,
                "end": end,
                "htmlLink": item.get("htmlLink", ""),
            })

        if not events:
            return {
                "count": 0,
                "events": [],
                "summary": "You have no upcoming events on your calendar for this time period.",
            }

        lines = []
        for ev in events:
            start_str = ev["start"]
            try:
                # Format friendly start time
                dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                formatted_time = dt.strftime("%A, %b %d at %I:%M %p")
            except Exception:
                formatted_time = start_str
            lines.append(f"- {ev['summary']} ({formatted_time})")

        return {
            "count": len(events),
            "events": events,
            "summary": f"You have {len(events)} upcoming events:\n" + "\n".join(lines),
        }

    def add_calendar_event(
        self,
        summary: str,
        start_time: str,
        end_time: str | None = None,
        description: str = "",
        location: str = "",
    ) -> dict[str, Any]:
        """Create a new event on Google Calendar.

        Accepts ISO strings or relative date/times.
        """
        # Parse start_time or fallback to sensible default
        try:
            if "T" in start_time:
                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            else:
                start_dt = datetime.fromisoformat(start_time)
        except Exception:
            # Try parsing natural format or default to 1 hour from now
            start_dt = datetime.now(timezone.utc) + timedelta(hours=1)

        if end_time:
            try:
                if "T" in end_time:
                    end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                else:
                    end_dt = datetime.fromisoformat(end_time)
            except Exception:
                end_dt = start_dt + timedelta(hours=1)
        else:
            end_dt = start_dt + timedelta(hours=1)

        event_body: dict[str, Any] = {
            "summary": summary.strip(),
            "description": description.strip(),
            "location": location.strip(),
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()},
        }

        url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
        created = self._api_request(url, method="POST", json_data=event_body)

        friendly_start = start_dt.strftime("%A, %b %d at %I:%M %p")
        return {
            "status": "created",
            "id": created.get("id"),
            "summary": summary,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "link": created.get("htmlLink", ""),
            "message": f"Added '{summary}' to your Google Calendar for {friendly_start}.",
        }

    def quick_add_calendar_event(self, text: str) -> dict[str, Any]:
        """Quickly add an event using Google Calendar natural language text."""
        params = {"text": text.strip()}
        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/quickAdd?{urlencode(params)}"
        created = self._api_request(url, method="POST")
        summary = created.get("summary", text)
        start = created.get("start", {}).get("dateTime") or created.get("start", {}).get("date", "")
        return {
            "status": "created",
            "id": created.get("id"),
            "summary": summary,
            "start": start,
            "link": created.get("htmlLink", ""),
            "message": f"Added event '{summary}' to your Google Calendar.",
        }

    # -----------------------------------------------------------------------
    # GMAIL EXTRAS
    # -----------------------------------------------------------------------

    def trash_email(self, message_id: str) -> dict[str, Any]:
        """Move an email to trash by message ID."""
        self._api_request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/trash",
            method="POST",
        )
        return {
            "status": "trashed",
            "id": message_id,
            "message": "Email moved to trash.",
        }

    def mark_email_read(self, message_id: str, read: bool = True) -> dict[str, Any]:
        """Mark an email as read or unread."""
        body = {"removeLabelIds": ["UNREAD"]} if read else {"addLabelIds": ["UNREAD"]}
        self._api_request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/modify",
            method="POST",
            json_data=body,
        )
        state = "marked as read" if read else "marked as unread"
        return {"status": state, "id": message_id, "message": f"Email {state}."}

    # -----------------------------------------------------------------------
    # GOOGLE CALENDAR EXTRAS
    # -----------------------------------------------------------------------

    @staticmethod
    def _parse_datetime(value: str, fallback: datetime) -> datetime:
        try:
            if "T" in value:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            return datetime.fromisoformat(value)
        except Exception:
            return fallback

    def delete_calendar_event(self, event_id: str) -> dict[str, Any]:
        """Delete an event from the primary calendar by event ID."""
        self._api_request(
            f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
            method="DELETE",
        )
        return {
            "status": "deleted",
            "id": event_id,
            "message": "Event removed from your Google Calendar.",
        }

    def update_calendar_event(
        self,
        event_id: str,
        summary: str = "",
        start_time: str = "",
        end_time: str = "",
        description: str = "",
        location: str = "",
    ) -> dict[str, Any]:
        """Update fields of an existing calendar event. Only provided fields change."""
        patch: dict[str, Any] = {}
        now = datetime.now(timezone.utc)
        if summary.strip():
            patch["summary"] = summary.strip()
        if description.strip():
            patch["description"] = description.strip()
        if location.strip():
            patch["location"] = location.strip()
        if start_time.strip():
            patch["start"] = {"dateTime": self._parse_datetime(start_time, now).isoformat()}
        if end_time.strip():
            patch["end"] = {"dateTime": self._parse_datetime(end_time, now).isoformat()}
        if not patch:
            raise RuntimeError("No changes provided for the calendar event.")

        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}"
        updated = self._api_request(url, method="PATCH", json_data=patch)
        return {
            "status": "updated",
            "id": updated.get("id", event_id),
            "summary": updated.get("summary", ""),
            "link": updated.get("htmlLink", ""),
            "message": f"Updated calendar event '{updated.get('summary', '')}'.",
        }

    # -----------------------------------------------------------------------
    # GOOGLE DRIVE TOOLS
    # -----------------------------------------------------------------------

    DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"

    def _find_drive_item(self, name: str, mime_type: str | None = None) -> str | None:
        clause = f"name = '{name.strip().replace(chr(39), chr(92) + chr(39))}' and trashed = false"
        if mime_type:
            clause += f" and mimeType = '{mime_type}'"
        params = {
            "q": clause,
            "pageSize": 1,
            "fields": "files(id,name)",
            "supportsAllDrives": "true",
        }
        data = self._api_request(f"https://www.googleapis.com/drive/v3/files?{urlencode(params)}")
        files = data.get("files", [])
        return files[0]["id"] if files else None

    def drive_list_files(self, query: str = "", max_results: int = 15) -> dict[str, Any]:
        """Search or list files in Google Drive (excludes trashed items)."""
        limit = max(1, min(max_results, 50))
        clauses = ["trashed = false"]
        if query.strip():
            safe = query.strip().replace("'", "\\'")
            clauses.append(f"name contains '{safe}'")
        params = {
            "q": " and ".join(clauses),
            "pageSize": limit,
            "fields": "files(id,name,mimeType,size,modifiedTime,webViewLink)",
            "orderBy": "modifiedTime desc",
            "supportsAllDrives": "true",
        }
        data = self._api_request(f"https://www.googleapis.com/drive/v3/files?{urlencode(params)}")
        files = [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "type": item.get("mimeType"),
                "size_bytes": item.get("size", ""),
                "modified": item.get("modifiedTime", ""),
                "link": item.get("webViewLink", ""),
            }
            for item in data.get("files", [])
        ]
        if not files:
            return {"count": 0, "files": [], "summary": f"No Drive files matched '{query or 'your drive'}'."}
        lines = [f"- {item['name']} ({item['type']}) {item['link']}" for item in files]
        return {
            "count": len(files),
            "files": files,
            "summary": f"Found {len(files)} Drive files:\n" + "\n".join(lines),
        }

    def drive_upload_file(self, local_path: str, name: str = "", parent_folder: str = "") -> dict[str, Any]:
        """Upload a local file to Google Drive via multipart upload."""
        path = Path(local_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {local_path}")

        meta: dict[str, Any] = {"name": name.strip() or path.name}
        if parent_folder.strip():
            folder_id = self._find_drive_item(parent_folder, self.DRIVE_FOLDER_MIME)
            if not folder_id:
                raise RuntimeError(f"Could not find a Drive folder named '{parent_folder}'.")
            meta["parents"] = [folder_id]

        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        boundary = "jarvis_drive_upload_37f2a9"
        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(meta)}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8") + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")

        url = (
            "https://www.googleapis.com/upload/drive/v3/files"
            "?uploadType=multipart&supportsAllDrives=true&fields=id,name,webViewLink,size"
        )
        result = self._api_request(
            url,
            method="POST",
            raw_data=body,
            content_type=f"multipart/related; boundary={boundary}",
            timeout=300,
        )
        size_kb = max(1, path.stat().st_size // 1024)
        return {
            "status": "uploaded",
            "id": result.get("id"),
            "name": result.get("name"),
            "size_kb": size_kb,
            "link": result.get("webViewLink", ""),
            "message": f"Uploaded '{result.get('name', path.name)}' ({size_kb} KB) to Google Drive.",
        }

    def drive_download_file(self, file_id: str, dest_dir: str = "") -> dict[str, Any]:
        """Download a Drive file to this PC by its ID."""
        meta = self._api_request(
            f"https://www.googleapis.com/drive/v3/files/{file_id}?supportsAllDrives=true&fields=name,mimeType"
        )
        file_name = meta.get("name") or f"drive_{file_id}"
        target_dir = Path(dest_dir).expanduser() if dest_dir.strip() else BASE_DIR / "data" / "drive_downloads"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / file_name

        token = self.get_valid_access_token()
        req = Request(
            f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&supportsAllDrives=true",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urlopen(req, timeout=300) as res, open(target, "wb") as fh:
                fh.write(res.read())
        except HTTPError as e:
            raise RuntimeError(
                f"Drive download failed ({e.code}). Google Docs/Sheets formats must be exported, not downloaded directly."
            ) from e

        size_kb = max(1, target.stat().st_size // 1024)
        return {
            "status": "downloaded",
            "path": str(target),
            "name": file_name,
            "size_kb": size_kb,
            "message": f"Downloaded '{file_name}' ({size_kb} KB) to {target}.",
        }

    def drive_create_folder(self, name: str, parent_folder: str = "") -> dict[str, Any]:
        """Create a folder in Google Drive."""
        meta: dict[str, Any] = {"name": name.strip(), "mimeType": self.DRIVE_FOLDER_MIME}
        if parent_folder.strip():
            folder_id = self._find_drive_item(parent_folder, self.DRIVE_FOLDER_MIME)
            if not folder_id:
                raise RuntimeError(f"Could not find a Drive folder named '{parent_folder}'.")
            meta["parents"] = [folder_id]

        created = self._api_request(
            "https://www.googleapis.com/drive/v3/files?fields=id,name,webViewLink&supportsAllDrives=true",
            method="POST",
            json_data=meta,
        )
        return {
            "status": "created",
            "id": created.get("id"),
            "name": created.get("name"),
            "link": created.get("webViewLink", ""),
            "message": f"Created Drive folder '{created.get('name', name)}'.",
        }

    def drive_share_file(self, file_id: str) -> dict[str, Any]:
        """Make a Drive file viewable by anyone with the link and return the link."""
        self._api_request(
            f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions?supportsAllDrives=true",
            method="POST",
            json_data={"role": "reader", "type": "anyone"},
        )
        meta = self._api_request(
            f"https://www.googleapis.com/drive/v3/files/{file_id}?supportsAllDrives=true&fields=name,webViewLink"
        )
        link = meta.get("webViewLink", "")
        return {
            "status": "shared",
            "name": meta.get("name", ""),
            "link": link,
            "message": f"'{meta.get('name', 'File')}' is now shared — anyone with this link can view it: {link}",
        }

    def drive_delete_file(self, file_id: str) -> dict[str, Any]:
        """Move a Drive file to trash by its ID."""
        self._api_request(
            f"https://www.googleapis.com/drive/v3/files/{file_id}?supportsAllDrives=true",
            method="PATCH",
            json_data={"trashed": True},
        )
        return {
            "status": "trashed",
            "id": file_id,
            "message": "Drive item moved to trash.",
        }

    # -----------------------------------------------------------------------
    # GOOGLE DOCS TOOLS
    # -----------------------------------------------------------------------

    def docs_get_document(self, document_id: str) -> dict[str, Any]:
        """Get a Google Doc by ID."""
        url = f"https://docs.googleapis.com/v1/documents/{document_id}"
        doc = self._api_request(url)
        return {
            "document_id": doc.get("documentId"),
            "title": doc.get("title"),
            "body": doc.get("body", {}),
            "revision_id": doc.get("revisionId"),
        }

    def docs_read_document(self, document_id: str) -> dict[str, Any]:
        """Read the text content of a Google Doc."""
        url = f"https://docs.googleapis.com/v1/documents/{document_id}"
        doc = self._api_request(url)
        content = self._extract_text_from_doc(doc)
        return {
            "document_id": doc.get("documentId"),
            "title": doc.get("title"),
            "content": content,
            "revision_id": doc.get("revisionId"),
        }

    def _extract_text_from_doc(self, doc: dict[str, Any]) -> str:
        """Extract plain text from a Google Doc JSON structure."""
        text_parts = []
        body = doc.get("body", {})
        content = body.get("content", [])
        for element in content:
            if "paragraph" in element:
                for pe in element["paragraph"].get("elements", []):
                    text_run = pe.get("textRun")
                    if text_run:
                        text_parts.append(text_run.get("content", ""))
            elif "table" in element:
                for row in element["table"].get("tableRows", []):
                    for cell in row.get("tableCells", []):
                        for cell_content in cell.get("content", []):
                            if "paragraph" in cell_content:
                                for pe in cell_content["paragraph"].get("elements", []):
                                    text_run = pe.get("textRun")
                                    if text_run:
                                        text_parts.append(text_run.get("content", ""))
        return "".join(text_parts)

    def docs_create_document(self, title: str, text: str = "") -> dict[str, Any]:
        """Create a new Google Doc with optional initial text."""
        body = {"title": title.strip()}
        if text.strip():
            body["body"] = {"content": [{"paragraph": {"elements": [{"textRun": {"content": text}}]}}]}
        url = "https://docs.googleapis.com/v1/documents"
        created = self._api_request(url, method="POST", json_data=body)
        return {
            "status": "created",
            "document_id": created.get("documentId"),
            "title": created.get("title"),
            "link": f"https://docs.google.com/document/d/{created.get('documentId')}/edit",
            "message": f"Created Google Doc '{created.get('title', title)}'.",
        }

    def docs_append_text(self, document_id: str, text: str) -> dict[str, Any]:
        """Append text to the end of a Google Doc."""
        requests = [{
            "insertText": {
                "endOfSegmentLocation": {},
                "text": text,
            }
        }]
        url = f"https://docs.googleapis.com/v1/documents/{document_id}:batchUpdate"
        self._api_request(url, method="POST", json_data={"requests": requests})
        return {
            "status": "appended",
            "document_id": document_id,
            "message": f"Appended text to document {document_id}.",
        }

    def docs_replace_text(self, document_id: str, search_text: str, replace_text: str, match_case: bool = False) -> dict[str, Any]:
        """Replace all occurrences of search_text with replace_text in a Google Doc."""
        requests = [{
            "replaceAllText": {
                "containsText": {
                    "text": search_text,
                    "matchCase": match_case,
                },
                "replaceText": replace_text,
            }
        }]
        url = f"https://docs.googleapis.com/v1/documents/{document_id}:batchUpdate"
        result = self._api_request(url, method="POST", json_data={"requests": requests})
        return {
            "status": "replaced",
            "document_id": document_id,
            "replacements": result.get("replies", []),
            "message": f"Replaced '{search_text}' with '{replace_text}' in document {document_id}.",
        }

    # -----------------------------------------------------------------------
    # GOOGLE SHEETS TOOLS
    # -----------------------------------------------------------------------

    def sheets_get_spreadsheet(self, spreadsheet_id: str) -> dict[str, Any]:
        """Get a Google Spreadsheet by ID."""
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
        sheet = self._api_request(url)
        sheets_info = []
        for s in sheet.get("sheets", []):
            props = s.get("properties", {})
            sheets_info.append({
                "sheet_id": props.get("sheetId"),
                "title": props.get("title"),
                "index": props.get("index"),
                "row_count": props.get("gridProperties", {}).get("rowCount"),
                "column_count": props.get("gridProperties", {}).get("columnCount"),
            })
        return {
            "spreadsheet_id": sheet.get("spreadsheetId"),
            "title": sheet.get("properties", {}).get("title"),
            "sheets": sheets_info,
            "url": f"https://docs.google.com/spreadsheets/d/{sheet.get('spreadsheetId')}/edit",
        }

    def sheets_read_range(self, spreadsheet_id: str, range_a1: str) -> dict[str, Any]:
        """Read values from a range in a Google Sheet (A1 notation)."""
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{range_a1}"
        data = self._api_request(url)
        return {
            "spreadsheet_id": spreadsheet_id,
            "range": data.get("range"),
            "major_dimension": data.get("majorDimension", "ROWS"),
            "values": data.get("values", []),
        }

    def sheets_write_range(self, spreadsheet_id: str, range_a1: str, values: list[list[Any]], value_input_option: str = "USER_ENTERED") -> dict[str, Any]:
        """Write values to a range in a Google Sheet (A1 notation)."""
        body = {
            "valueInputOption": value_input_option,
            "data": [{"range": range_a1, "values": values, "majorDimension": "ROWS"}],
        }
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values:batchUpdate"
        result = self._api_request(url, method="POST", json_data=body)
        return {
            "status": "written",
            "spreadsheet_id": spreadsheet_id,
            "updated_cells": result.get("totalUpdatedCells"),
            "updated_rows": result.get("totalUpdatedRows"),
            "updated_columns": result.get("totalUpdatedColumns"),
        }

    def sheets_append_rows(self, spreadsheet_id: str, range_a1: str, values: list[list[Any]], value_input_option: str = "USER_ENTERED") -> dict[str, Any]:
        """Append rows to a Google Sheet."""
        body = {
            "valueInputOption": value_input_option,
            "insertDataOption": "INSERT_ROWS",
            "values": values,
        }
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{range_a1}:append"
        result = self._api_request(url, method="POST", json_data=body)
        return {
            "status": "appended",
            "spreadsheet_id": spreadsheet_id,
            "updated_range": result.get("updates", {}).get("updatedRange"),
            "updated_rows": result.get("updates", {}).get("updatedRows"),
        }

    def sheets_create_spreadsheet(self, title: str, sheets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Create a new Google Spreadsheet."""
        body = {"properties": {"title": title.strip()}}
        if sheets:
            body["sheets"] = sheets
        url = "https://sheets.googleapis.com/v4/spreadsheets"
        created = self._api_request(url, method="POST", json_data=body)
        return {
            "status": "created",
            "spreadsheet_id": created.get("spreadsheetId"),
            "title": created.get("properties", {}).get("title"),
            "url": f"https://docs.google.com/spreadsheets/d/{created.get('spreadsheetId')}/edit",
            "message": f"Created Google Sheet '{created.get('properties', {}).get('title', title)}'.",
        }

    def sheets_add_sheet(self, spreadsheet_id: str, title: str, rows: int = 1000, cols: int = 26) -> dict[str, Any]:
        """Add a new sheet to an existing spreadsheet."""
        body = {
            "requests": [{
                "addSheet": {
                    "properties": {
                        "title": title.strip(),
                        "gridProperties": {"rowCount": rows, "columnCount": cols},
                    }
                }
            }]
        }
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate"
        result = self._api_request(url, method="POST", json_data=body)
        return {
            "status": "added",
            "spreadsheet_id": spreadsheet_id,
            "sheet_id": result.get("replies", [{}])[0].get("addSheet", {}).get("properties", {}).get("sheetId"),
            "title": title,
        }

    # -----------------------------------------------------------------------
    # GOOGLE TASKS TOOLS
    # -----------------------------------------------------------------------

    GOOGLE_TASKS_LISTS_URI = "https://tasks.googleapis.com/tasks/v1/users/@me/lists"

    def list_task_lists(self) -> dict[str, Any]:
        """List the user's Google Tasks task lists."""
        data = self._api_request(self.GOOGLE_TASKS_LISTS_URI)
        lists = [{"id": item.get("id"), "title": item.get("title")} for item in data.get("items", [])]
        summary = ", ".join(item["title"] or "(Untitled)" for item in lists) if lists else "No task lists found."
        return {"count": len(lists), "lists": lists, "summary": f"Your Google Tasks lists: {summary}"}

    def _default_task_list_id(self) -> str:
        lists = self.list_task_lists().get("lists", [])
        if not lists:
            raise RuntimeError("No Google Tasks lists found.")
        return lists[0]["id"]

    def list_tasks(self, list_id: str = "", show_completed: bool = False, max_results: int = 20) -> dict[str, Any]:
        """List tasks in a Google Tasks list (first list by default)."""
        lid = list_id.strip() or self._default_task_list_id()
        params = {
            "showCompleted": "true" if show_completed else "false",
            "maxResults": max(1, min(max_results, 100)),
        }
        data = self._api_request(f"{self.GOOGLE_TASKS_LISTS_URI}/{lid}/tasks?{urlencode(params)}")
        tasks = [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "due": item.get("due", ""),
                "status": item.get("status"),
                "notes": item.get("notes", ""),
            }
            for item in data.get("items", [])
        ]
        if not tasks:
            return {"count": 0, "tasks": [], "summary": "No tasks found in that list."}
        lines = [f"- {t['title']}" + (f" (due {t['due'][:10]})" if t["due"] else "") for t in tasks]
        return {
            "count": len(tasks),
            "tasks": tasks,
            "summary": f"You have {len(tasks)} tasks:\n" + "\n".join(lines),
        }

    def create_task(self, title: str, due: str = "", list_id: str = "") -> dict[str, Any]:
        """Add a task to a Google Tasks list with optional due date."""
        lid = list_id.strip() or self._default_task_list_id()
        body: dict[str, Any] = {"title": title.strip()}
        if due.strip():
            parsed = self._parse_datetime(due, datetime.now(timezone.utc))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            body["due"] = parsed.isoformat()

        created = self._api_request(
            f"{self.GOOGLE_TASKS_LISTS_URI}/{lid}/tasks",
            method="POST",
            json_data=body,
        )
        friendly_due = created.get("due", "")[:16].replace("T", " ")
        return {
            "status": "created",
            "id": created.get("id"),
            "title": created.get("title"),
            "due": created.get("due", ""),
            "message": f"Added '{created.get('title', title)}' to Google Tasks" + (f" (due {friendly_due})." if friendly_due else "."),
        }

    def delete_task(self, task_id: str, list_id: str = "") -> dict[str, Any]:
        """Delete a Google Tasks task by ID (default list when not given)."""
        lid = (list_id or "").strip() or self._default_task_list_id()
        self._api_request(
            f"{self.GOOGLE_TASKS_LISTS_URI}/{lid}/tasks/{task_id}",
            method="DELETE",
        )
        return {"status": "deleted", "id": task_id, "message": "Task deleted from Google Tasks."}

    # -----------------------------------------------------------------------
    # GOOGLE CONTACTS TOOLS
    # -----------------------------------------------------------------------

    def search_contacts(self, query: str, max_results: int = 5) -> dict[str, Any]:
        """Search Google Contacts by name, email, or phone number."""
        params = {
            "query": query.strip(),
            "readMask": "names,emailAddresses,phoneNumbers",
            "pageSize": max(1, min(max_results, 30)),
        }
        data = self._api_request(f"https://people.googleapis.com/v1/people:searchContacts?{urlencode(params)}")
        people = []
        for entry in data.get("results", []):
            person = entry.get("person", {})
            names = person.get("names", [])
            people.append({
                "name": names[0].get("displayName", "") if names else "",
                "emails": [e.get("value") for e in person.get("emailAddresses", [])],
                "phones": [p.get("value") for p in person.get("phoneNumbers", [])],
            })
        if not people:
            return {"count": 0, "contacts": [], "summary": f"No contacts matched '{query}'."}
        lines = [
            f"- {c['name']}: {', '.join(c['emails']) or 'no email'}"
            for c in people
        ]
        return {
            "count": len(people),
            "contacts": people,
            "summary": f"Found {len(people)} contacts:\n" + "\n".join(lines),
        }


# Force IPv4 for Google API calls: on this machine the IPv6 path to Google
# intermittently blackholes (12s+ stalls), while IPv4 connects in <1s.
try:
    import socket as _socket
    import urllib3.util.connection as _urllib3_conn

    def _prefer_ipv4():
        return _socket.AF_INET

    _urllib3_conn.allowed_gai_family = _prefer_ipv4
except Exception:
    pass


# Global singleton instance
google_workspace = GoogleWorkspaceConnector()
