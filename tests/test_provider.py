"""Tests for the `providers/claude` script: paths, token handling, the auth
JSON maki reads, and login input parsing."""

import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "providers" / "claude"
LOADER = importlib.machinery.SourceFileLoader("claude_provider", str(SCRIPT))
SPEC = importlib.util.spec_from_loader("claude_provider", LOADER)
provider = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(provider)


def isolated_home():
    home = tempfile.TemporaryDirectory()
    patch = mock.patch.dict(os.environ, {"HOME": home.name, "XDG_STATE_HOME": os.path.join(home.name, "st")})
    return home, patch


class PathsTest(unittest.TestCase):
    def test_state_dir_follows_xdg_and_the_legacy_directory(self):
        home, patch = isolated_home()
        with home, patch:
            self.assertEqual(provider.state_dir(), os.path.join(home.name, "st", "maki"))
            self.assertEqual(provider.token_path(), os.path.join(home.name, "st", "maki", "auth", "claude.json"))
            os.mkdir(os.path.join(home.name, ".maki"))
            self.assertEqual(provider.state_dir(), os.path.join(home.name, ".maki"))


class TokensTest(unittest.TestCase):
    def test_round_trip_is_private_and_validated(self):
        home, patch = isolated_home()
        with home, patch:
            self.assertIsNone(provider.load_tokens())
            tokens = {"access": "a", "refresh": "r", "expires": provider.now_ms() + 10_000}
            provider.save_tokens(tokens)
            self.assertEqual(provider.load_tokens(), tokens)
            self.assertEqual(os.stat(provider.token_path()).st_mode & 0o777, 0o600)
            self.assertFalse(provider.expires_within(tokens, 5))
            self.assertTrue(provider.expires_within(tokens, 20))
            provider.save_tokens({"access": "a"})
            self.assertIsNone(provider.load_tokens())

    def test_tokens_from_response_keeps_email_and_previous_refresh(self):
        fresh = provider.tokens_from_response(
            {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "account": {"email_address": "me@example.com"}}
        )
        self.assertEqual(fresh["email"], "me@example.com")
        self.assertGreater(fresh["expires"], provider.now_ms())
        rotated = provider.tokens_from_response({"access_token": "b", "expires_in": 60}, fresh)
        self.assertEqual(rotated["refresh"], "r")
        self.assertEqual(rotated["email"], "me@example.com")
        with self.assertRaises(provider.Fail):
            provider.tokens_from_response({"access_token": "a"})

    def test_auth_json_carries_only_the_bearer_header(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            provider.emit_auth({"access": "tok", "refresh": "r", "expires": 0})
        self.assertEqual(json.loads(out.getvalue()), {"headers": {"authorization": "Bearer tok"}})


class CommandsTest(unittest.TestCase):
    def test_info_declares_the_identity_prefix(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(provider.main(["claude", "info"]), 0)
        info = json.loads(out.getvalue())
        self.assertEqual(info["base"], "anthropic")
        self.assertTrue(info["has_auth"])
        self.assertEqual(info["system_prefix"], provider.IDENTITY)

    def test_resolve_and_status_without_tokens(self):
        home, patch = isolated_home()
        with home, patch:
            with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                self.assertEqual(provider.main(["claude", "resolve"]), 1)
            self.assertEqual(err.getvalue().strip(), provider.NOT_LOGGED_IN)
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(provider.main(["claude", "status"]), 0)
            self.assertEqual(json.loads(out.getvalue()), {"logged_in": False, "email": None, "expires_in_s": None})

    def test_unknown_command_is_usage(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(provider.main(["claude", "serve"]), 2)
            self.assertEqual(provider.main(["claude"]), 2)


class LoginInputTest(unittest.TestCase):
    def test_parse_authorization_input_accepts_every_shape(self):
        url = provider.REDIRECT_URI + "?code=abc&state=xyz"
        self.assertEqual(provider.parse_authorization_input(url), ("abc", "xyz"))
        self.assertEqual(provider.parse_authorization_input("abc#xyz\n"), ("abc", "xyz"))
        self.assertEqual(provider.parse_authorization_input("code=abc&state=xyz"), ("abc", "xyz"))
        self.assertEqual(provider.parse_authorization_input("  abc "), ("abc", None))
        self.assertEqual(provider.parse_authorization_input("   "), (None, None))


if __name__ == "__main__":
    unittest.main()
