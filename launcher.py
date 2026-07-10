import socket
import threading
import time
import urllib.request

import uvicorn
import webview

import main


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


def run_server(port: int):
    config = uvicorn.Config(
        app=main.app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()


def start_app():
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    if not wait_until_ready(url, timeout_sec=30.0):
        raise RuntimeError("本地服务启动失败，30 秒内未成功响应。")

    webview.create_window(
        title="MindSpine",
        url=url,
        width=1600,
        height=1000,
        min_size=(1280, 820),
        text_select=True,
    )
    webview.start(gui="edgechromium", debug=False)


if __name__ == "__main__":
    start_app()
