"""Apichat desktop application entry point."""

from __future__ import annotations

import socket
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

import uvicorn
import webview

from app.server import app

webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True


def _open_external_url(url: str) -> None:
    if not isinstance(url, str):
        return
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https", "mailto"}:
        return
    webbrowser.open(url)


class JsApi:
    def browse_folder(self) -> str:
        windows = webview.windows
        if not windows:
            return ""
        dialog = getattr(webview, "FileDialog", None)
        folder = dialog.FOLDER if dialog is not None else getattr(webview, "FOLDER_DIALOG", None)
        result = windows[0].create_file_dialog(folder)
        if result:
            return result[0]
        return ""

    def open_url(self, url: str) -> None:
        _open_external_url(url)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_for_server(url: str, timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.4)
            return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("Local UI server failed to start")


def main() -> None:
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    server.install_signal_handlers = False
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/"
    _wait_for_server(url)
    window = webview.create_window(
        "Apichat",
        url,
        width=1440,
        height=920,
        min_size=(1100, 700),
        js_api=JsApi(),
        text_select=True,
    )

    def on_loaded() -> None:
        current = window.get_current_url() or ""
        parsed = urllib.parse.urlparse(current)
        host = (parsed.hostname or "").lower()
        if host in {"127.0.0.1", "localhost"} and parsed.port == port:
            return
        if parsed.scheme not in {"http", "https"}:
            return
        _open_external_url(current)
        window.load_url(url)

    window.events.loaded += on_loaded
    webview.start()


if __name__ == "__main__":
    main()
