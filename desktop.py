"""Text2Audio as a desktop app: the Studio UI in a native window, no browser needed.

The window is the operating system's own web view (WKWebView on macOS, WebView2 on
Windows), so there is no Chrome/Firefox process or tab overhead. The same FastAPI
server runs in a background thread of this process; closing the window stops it.

    python desktop.py          (needs:  pip install pywebview)
"""
import os
import sys
import threading
import time
import urllib.request

HOST, PORT = "127.0.0.1", 8765
URL = f"http://{HOST}:{PORT}"


def _healthy() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/api/health", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def main() -> int:
    from backend import updater
    if updater.auto_update():
        updater.restart()                 # reload with the freshly installed code
    try:
        import webview
    except ImportError:
        print("The desktop window needs pywebview:  pip install pywebview", file=sys.stderr)
        return 1

    import uvicorn
    import server

    uv = None
    if not _healthy():                    # reuse a server that is already running
        uv = uvicorn.Server(uvicorn.Config(server.app, host=HOST, port=PORT, log_level="warning"))
        thread = threading.Thread(target=uv.run, daemon=True)
        thread.start()
        for _ in range(100):
            if _healthy():
                break
            time.sleep(0.1)
        else:
            print("Text2Audio server did not start", file=sys.stderr)
            return 1

    webview.settings["ALLOW_DOWNLOADS"] = True      # "Download" buttons save files
    webview.create_window("Text2Audio Studio", URL, width=1200, height=860, min_size=(900, 600))
    webview.start()

    if uv is not None:                    # window closed: stop the server (and any encoder it started)
        uv.should_exit = True
        thread.join(timeout=8)
    server.procs.terminate_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
