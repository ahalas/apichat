"""Apichat desktop application entry point."""

from __future__ import annotations

import socket
import threading
import time
import urllib.request

import uvicorn
import webview

from app.server import app


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
    webview.create_window(
        "Apichat",
        url,
        width=1440,
        height=920,
        min_size=(1100, 700),
        js_api=JsApi(),
    )
    webview.start()


if __name__ == "__main__":
    main()
