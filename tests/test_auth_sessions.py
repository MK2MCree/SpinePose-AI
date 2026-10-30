import unittest
from unittest.mock import patch
from mindspine.auth_sessions import AuthSessions


class AuthSessionsTests(unittest.TestCase):
    def test_expiry_and_logout_are_server_enforced(self):
        store = AuthSessions(':memory:', ttl_seconds=20)
        with patch('mindspine.auth_sessions.time.time', return_value=100):
            token = store.encode('user-a')
            other = store.encode('user-a')
            self.assertEqual(store.decode(token), 'user-a')
            store.revoke(token)
            self.assertIsNone(store.decode(token))
            self.assertEqual(store.decode(other), 'user-a')
        with patch('mindspine.auth_sessions.time.time', return_value=121):
            self.assertIsNone(store.decode(other))

    def test_only_digests_persist(self):
        store = AuthSessions(':memory:')
        token = store.encode('user-a')
        with store.connection() as db:
            self.assertNotIn(token, str(db.execute('SELECT * FROM auth_sessions').fetchall()))
        self.assertIsNone(store.decode(token + 'x'))
