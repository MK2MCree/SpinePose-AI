from __future__ import annotations

import unittest
import threading

from mindspine.sessions import CaseRegistry, ModelProxy


class DummyModel:
    def __init__(self):
        self.model_xray = object()
        self.model_rgb = object()
        self.current_mode = None
        self.current_cv_img = None
        self.current_slip_list = []


class SessionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.template = DummyModel()
        self.registry = CaseRegistry(self.template, ttl_seconds=3600, max_cases_per_session=4)
        self.proxy = ModelProxy(self.registry)

    def test_cases_and_sessions_are_isolated_but_models_are_shared(self):
        with self.registry.activate_session("a" * 32):
            self.registry.create_case("case-a")
            self.proxy.current_mode = "xray"
            model_a = self.registry.current_model()

        with self.registry.activate_session("b" * 32):
            self.registry.create_case("case-b")
            self.proxy.current_mode = "rgb"
            model_b = self.registry.current_model()

        self.assertIsNot(model_a, model_b)
        self.assertIs(model_a.model_xray, model_b.model_xray)
        self.assertEqual(model_a.current_mode, "xray")
        self.assertEqual(model_b.current_mode, "rgb")

    def test_unknown_case_is_rejected(self):
        with self.registry.activate_session("c" * 32):
            with self.assertRaises(KeyError):
                self.registry.select_case("missing")

    def test_concurrent_uploads_in_same_account_keep_request_case(self):
        created_a, created_b = threading.Event(), threading.Event()
        def first():
            with self.registry.activate_session('a' * 32):
                self.registry.create_case('case-a')
                created_a.set()
                self.assertTrue(created_b.wait(3))
                self.proxy.image_path = 'synthetic-a.png'
        def second():
            self.assertTrue(created_a.wait(3))
            with self.registry.activate_session('a' * 32):
                self.registry.create_case('case-b')
                self.proxy.image_path = 'synthetic-b.png'
                created_b.set()
        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        with self.registry.activate_session('a' * 32):
            self.assertEqual(self.registry.select_case('case-a').image_path, 'synthetic-a.png')
            self.assertEqual(self.registry.select_case('case-b').image_path, 'synthetic-b.png')


if __name__ == "__main__":
    unittest.main()
