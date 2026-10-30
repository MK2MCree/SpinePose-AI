"""Use a tiny ASGI app and a fake window; no GUI, model, or patient data."""
from __future__ import annotations

import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import launcher


class WindowEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        for handler in self.handlers:
            handler()


class LauncherLifecycleTests(unittest.TestCase):
    def application(self):
        started, stopped = threading.Event(), threading.Event()
        cleanup = Mock()

        async def app(scope, receive, send):
            if scope["type"] == "lifespan":
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        started.set()
                        await send({"type": "lifespan.startup.complete"})
                    elif message["type"] == "lifespan.shutdown":
                        cleanup()
                        stopped.set()
                        await send({"type": "lifespan.shutdown.complete"})
                        return
            elif scope["type"] == "http":
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"ok"})

        return SimpleNamespace(app=app, stop_local_workers=cleanup), started, stopped

    def view(self, failure=False):
        closed = WindowEvent()
        window = SimpleNamespace(events=SimpleNamespace(closed=closed))
        view = Mock()
        view.create_window.return_value = window
        def start(**_):
            if failure:
                raise RuntimeError("Synthetic GUI failure")
            closed.fire()
        view.start.side_effect = start
        return view

    def test_window_close_runs_real_uvicorn_lifespan_shutdown(self):
        application, started, stopped = self.application()
        launcher.start_app(application, self.view(), startup_timeout_sec=4, shutdown_timeout_sec=4)
        self.assertTrue(started.is_set())
        self.assertTrue(stopped.is_set())
        application.stop_local_workers.assert_called_once()

    def test_gui_failure_still_shuts_down_local_service(self):
        application, started, stopped = self.application()
        with self.assertRaisesRegex(RuntimeError, "Synthetic GUI failure"):
            launcher.start_app(application, self.view(failure=True), startup_timeout_sec=4, shutdown_timeout_sec=4)
        self.assertTrue(started.is_set())
        self.assertTrue(stopped.is_set())
        application.stop_local_workers.assert_called_once()

    def test_startup_timeout_cleans_workers_even_without_lifespan(self):
        application = SimpleNamespace(app=object(), stop_local_workers=Mock())
        fake_server = Mock()
        with patch.object(launcher, "create_server", return_value=fake_server), patch.object(launcher, "wait_until_ready", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Local service failed"):
                launcher.start_app(application, self.view(), startup_timeout_sec=.01, shutdown_timeout_sec=.1)
        self.assertTrue(fake_server.should_exit)
        application.stop_local_workers.assert_called_once()


if __name__ == "__main__":
    unittest.main()
