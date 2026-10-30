from __future__ import annotations

import unittest

from mindspine.auth import AccountValidationError, UserStore


class UserStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = UserStore(":memory:")

    def test_registers_and_authenticates_a_local_account(self):
        user = self.store.register("脊柱-User", "correct horse battery staple")

        self.assertIsNone(self.store.authenticate("脊柱-User", "incorrect password"))
        authenticated = self.store.authenticate("脊柱-User", "correct horse battery staple")

        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.user_id, user.user_id)
        self.assertEqual(self.store.get_user(user.user_id).username, "脊柱-User")

    def test_username_is_unique_case_insensitively(self):
        self.store.register("SpineUser", "correct horse battery staple")

        with self.assertRaises(AccountValidationError):
            self.store.register("spineuser", "another correct password")

    def test_rejects_short_passwords_and_invalid_usernames(self):
        with self.assertRaises(AccountValidationError):
            self.store.register("a", "correct horse battery staple")
        with self.assertRaises(AccountValidationError):
            self.store.register("valid_name", "short")


if __name__ == "__main__":
    unittest.main()
