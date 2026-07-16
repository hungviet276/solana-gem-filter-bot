from __future__ import annotations

import unittest

from fastapi import HTTPException

from gem_filter.api.auth import decode_token, hash_password, issue_token, verify_password


class AuthTests(unittest.TestCase):
    def test_password_hash_and_token_lifecycle(self) -> None:
        salt, digest = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", salt, digest))
        self.assertFalse(verify_password("wrong", salt, digest))
        token = issue_token("admin@example.invalid", "Admin", "s" * 40)
        claims = decode_token(token, "s" * 40)
        self.assertEqual(claims["role"], "Admin")

    def test_invalid_or_expired_token_is_rejected(self) -> None:
        with self.assertRaises(HTTPException):
            decode_token("invalid", "s" * 40)
        expired = issue_token("a", "Viewer", "s" * 40, ttl_seconds=-1)
        with self.assertRaises(HTTPException):
            decode_token(expired, "s" * 40)


if __name__ == "__main__":
    unittest.main()
