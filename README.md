# Apichat

A desktop chat app for **xAI** and **OpenRouter**, using your own API keys. The window is a modern HTML UI inside WebView2.

## Features

- Streaming chat with markdown replies
- Image and video generation (xAI Imagine)
- Model and reasoning effort selection
- Local chat history
- Settings for API keys, hidden models, and a save folder

## Setup

```powershell
cd C:\Users\Owner\agent-chat
pip install -r requirements.txt
python main.py
```

Or double-click `run.bat`.

## Desktop shortcut

`Apichat.exe` on the Desktop. Rebuild with `build_exe.bat`.

## First run

1. Click **Settings**.
2. Enter an API key, test the connection, uncheck models you do not want.
3. Pick a save folder, then **Save**.

Data lives in `%APPDATA%\AgentChat\`.
