# AGENTS.md

## Cursor Cloud specific instructions

Apichat is a **Windows desktop chat client** (`main.py` → `pywebview`/WebView2) whose real product logic is a FastAPI backend (`app/server.py`) that serves the HTML/JS UI in `web/`, persists chats in SQLite, and calls the xAI / OpenRouter APIs. General setup/run commands are in `README.md`.

Non-obvious notes for developing/testing on this Linux cloud VM:

- **Do not run `main.py` here.** It launches a native WebView2 window via `pywebview`, which needs a Windows/desktop display and will not work headless. Instead run the backend directly and use a browser:
  `python3 -m uvicorn app.server:app --host 127.0.0.1 --port 8000` then open `http://127.0.0.1:8000/`.
- **Python deps are installed into the system interpreter** (user site) by the update script using `--break-system-packages`. Run everything with `python3` (no virtualenv is required). A stray `.venv/` may exist from earlier setup; it is gitignored and optional.
- **AI features require the user's own API key.** Chat/Image/Video need a valid xAI or OpenRouter key entered at runtime via the in-app **Settings** modal (there is no env-var config). Keys/config/history are stored under `~/AgentChat/` (on Linux `APPDATA` is unset, so `app/config.py` falls back to the home dir); exports go to `~/Documents/AgentChat/outputs/`. Without a key, `/api/send` returns a graceful SSE error ("Add your xAI API key in Settings.") — this is expected, not a bug.
- **`os.startfile` is Windows-only.** The `/api/save` endpoint (when `open_after=true`) and `/api/open-folder` call it and will raise on Linux. When testing file export via the API, pass `open_after=false`.
- **No test suite or linter is configured.** Use `python3 -m compileall app main.py` as a basic sanity/compile check.
