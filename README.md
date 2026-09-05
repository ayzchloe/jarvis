# JARVIS — Laravel command center + FastAPI desktop core

This project is split into two local applications:

- `frontend/` is the **Laravel/PHP UI**, with the dashboard in `frontend/resources/views/dashboard.php`.
- `main.py` is the **FastAPI desktop-control backend**. It handles Groq tool calls, web search, app launching, system control, and explicit approvals for high-impact actions.

## Install prerequisites

You need Python 3.10+, PHP 8.2+, and Composer.

## Backend setup

1. Add your Groq key to the root `.env`:
   ```env
   GROQ_API_KEY=your_key_here
   ```
2. Install Python dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
3. Start the desktop backend:
   ```powershell
   python main.py
   ```

The API runs at `http://127.0.0.1:8100`.

## Laravel frontend setup

1. In a second terminal, enter the frontend folder:
   ```powershell
   cd frontend
   ```
2. Create the Laravel environment file and install packages:
   ```powershell
   Copy-Item .env.example .env
   composer install
   php artisan key:generate
   ```
3. Serve the PHP dashboard:
   ```powershell
   php artisan serve --host=127.0.0.1 --port=9999
   ```
4. Open `http://127.0.0.1:9999`.

The Laravel app calls the local backend through `/api/command`, `/api/actions/confirm`, `/api/speak`, and `/api/memory`; set `JARVIS_BACKEND_URL` in `frontend/.env` only if you change the Python backend address.

## Desktop capabilities

- Current web searches, websites, configured or Start Menu applications, folders, screenshots, volume, system statistics, and active-process lists.
- File reads, new-file creation, non-destructive terminal commands, closing apps, PC lock, shutdown/restart/sleep, and keyboard/mouse automation.
- Shutdown, restart, and sleep show an approval card and expire after five minutes. Existing files are never overwritten by voice control, destructive terminal commands are blocked, and pointer automation keeps PyAutoGUI's corner failsafe enabled.

## Add application aliases

Put reliable executable paths in `apps_config.json` for any app not found through the Start Menu or your system `PATH`:

```json
{
  "cursor": "%LOCALAPPDATA%\\Programs\\cursor\\Cursor.exe",
  "notepad": "notepad.exe"
}
```

`pyautogui` is included for approved keyboard/mouse controls. It deliberately needs exact click coordinates—JARVIS does not guess where to click on your screen.

## Continuous voice and connectors

Press **ACTIVATE VOICE** once in the Laravel dashboard to let the browser re-open speech recognition after each completed reply. Say **"stop listening"** to pause it. Browser security requires that first click and that the tab remains open; it cannot listen in the background after the browser or page is closed.

The dashboard is voice-only. JARVIS speaks through the local Windows SAPI voice engine first, preferring an installed male voice with a slower, lower pitch; browser speech is only a fallback. If no male Windows voice is installed, Windows chooses its default system voice. Chrome remains the active browser for the current voice session: after saying "open Chrome", a later "open YouTube" command opens YouTube in Chrome rather than the default browser.

Only shutdown, restart, and sleep require confirmation. Other explicitly requested local actions run directly; destructive terminal commands are still blocked.

## Instant local commands

Common commands bypass Groq so they remain responsive if the AI service is slow or unavailable. Say **"open Downloads"**, **"open File Explorer"**, **"open Spotify"** (or another configured app), **"search the web for ..."**, **"set volume to 40"**, **"mute"**, **"lock my PC"**, **"close Chrome"**, or **"run a system check"**. Web searches and known websites open in the current Chrome session.

Terminal commands still run only when you explicitly ask for one. JARVIS returns the exit code and diagnostic output on failure instead of incorrectly reporting success, and it rejects deletion, disk-formatting, power, registry-delete, and encoded-command patterns.

## Second-brain memory

JARVIS keeps explicit long-term memories locally in `data/jarvis_memory.json`; it is excluded from Git alongside `.env`. Use natural voice phrases:

- "Remember that I prefer dark mode."
- "Remember my project goal is to ship the dashboard on Friday."
- "What do you remember about my project goal?"
- "Forget my project goal."

Only facts you explicitly ask it to remember are saved. Relevant memories are supplied to later requests, but JARVIS does not silently save ordinary conversation or screenshots.

JARVIS also maintains an active focus queue: say **"Remind me to finish the dashboard"**, **"What should I focus on?"**, **"Complete finish the dashboard"**, or **"Give me a daily briefing"**. The queue appears live in the right side of the voice dashboard.

## Restart after updates

Use the one-command launcher from the project root to load the newest backend and Laravel dashboard:

```powershell
.\restart_jarvis.ps1
```

It stops only the local services using ports `8100` and `9999`, starts their updated versions, and opens the dashboard.

`auth.py` lists the connector prerequisites. Copy the required values from `connectors.env.example` into the root `.env` only for services you want to connect. JARVIS currently reports credential readiness; OAuth callback flows and provider actions should be added only after you supply the corresponding OAuth app credentials:

- **Google:** Google Cloud OAuth client ID, client secret, and redirect URI; enable the Gmail, Calendar, Drive, and People APIs you intend to use.
- **Microsoft:** Entra app client ID, client secret, tenant ID, and redirect URI; grant the Microsoft Graph permissions you intend to use.
- **Slack:** Slack app client ID, client secret, redirect URI, and the required bot/user scopes.
- **GitHub:** GitHub OAuth client ID, client secret, and redirect URI.
- **Jira:** Atlassian 3LO OAuth client ID, client secret, redirect URI, and cloud ID.

The existing `GROQ_API_KEY` is the only required key for local JARVIS PC controls and reasoning.
