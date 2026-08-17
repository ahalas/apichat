# Apichat

A desktop chat app for **xAI** and **OpenRouter**, using your own API keys. The window is a modern HTML UI inside WebView2.

## Features

- Streaming chat with markdown replies
- Image and video generation (xAI Imagine, or OpenRouter image/video models)
- File attachments and web search (including OpenRouter web search and PDF parsing)
- Word/Excel converted to text or CSV on send
- Model and reasoning effort selection
- Local chat history
- Settings for API keys, hidden models, and a save folder

## Setup

```powershell
pip install -r requirements.txt
python main.py
```

On first launch, open **Settings**, add an API key, test the connection, and save.

## First run

1. Click **Settings** (or **Open Settings** on the empty screen).
2. Enter an API key, test the connection, uncheck models you do not want.
3. Pick a save folder, then **Save**.

Data lives in `%APPDATA%\Apichat\` (existing `%APPDATA%\AgentChat\` data is reused).
