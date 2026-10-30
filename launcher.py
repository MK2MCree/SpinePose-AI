import socket
import threading
import time
import urllib.request

import uvicorn


def find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_until_ready(url: str, timeout_sec: float = 30.0):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if 200 <= resp.status < 500:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def create_server(port: int, application):
    config = uvicorn.Config(
        app=application,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        timeout_graceful_shutdown=5,
    )
    return uvicorn.Server(config)


def start_app(app_module=None, view_module=None, *, startup_timeout_sec=30.0, shutdown_timeout_sec=30.0):
    # Lazy imports keep launcher lifecycle tests independent of model loading.
    if app_module is None:
        import main as app_module
    if view_module is None:
        import webview as view_module
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    server = create_server(port, app_module.app)
    server_thread = threading.Thread(target=server.run, name="mindspine-http", daemon=True)
    server_thread.start()
    ready = False
    try:
        ready = wait_until_ready(url + "/health/live", timeout_sec=startup_timeout_sec)
        if not ready:
            raise RuntimeError("Local service failed to start within 30 seconds.")
        window = view_module.create_window(
            title="MindSpine",
            url=url,
            width=1600,
            height=1000,
            min_size=(1280, 820),
            text_select=True,
        )

        def request_shutdown(*_):
            server.should_exit = True

        window.events.closed += request_shutdown
        view_module.start(gui="edgechromium", debug=False)
    finally:
        # Request Uvicorn's normal lifespan shutdown while Python is still alive.
        # A daemon thread alone would skip main.stop_local_workers on GUI exit.
        server.should_exit = True
        server_thread.join(timeout=shutdown_timeout_sec)
        if server_thread.is_alive() or not ready:
            # Startup failures and stuck HTTP handlers must not orphan GPU/ffmpeg.
            cleanup = getattr(app_module, "stop_local_workers", None)
            if cleanup is not None:
                cleanup()
            server.force_exit = True
            server_thread.join(timeout=2.0)


if __name__ == "__main__":
    start_app()
