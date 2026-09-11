"""Tests for the `providers/claude` script: wire rewriting, the SSE rewriter,
paths, token handling, and the proxy end to end against a fake upstream."""

import http.client
import http.server
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import tempfile
import threading
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "providers" / "claude"
LOADER = importlib.machinery.SourceFileLoader("claude_provider", str(SCRIPT))
SPEC = importlib.util.spec_from_loader("claude_provider", LOADER)
provider = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(provider)

BEARER = "Bearer test-token"
UPSTREAM_EVENTS = [
    {"type": "message_start", "message": {"id": "msg_1"}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "t1", "name": "mcp__maki__todo_write", "input": {}}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"todos\":[]}"}},
    {"type": "content_block_stop", "index": 1},
    {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "t2", "name": "mcp__maki__batch", "input": {}}},
    {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": "{\"tool_calls\":[{\"tool\":\"Gl"}},
    {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": "ob\",\"parameters\":{\"pattern\":\"*\"}},{\"tool\":\"mcp__maki__index\"}]}"}},
    {"type": "content_block_stop", "index": 2},
    {"type": "message_stop"},
]


def sse(events):
    return b"".join(
        b"event: %s\ndata: %s\n\n" % (e["type"].encode(), json.dumps(e).encode()) for e in events
    )


def parse_sse(raw):
    events = []
    for record in raw.decode().split("\n\n"):
        for line in record.split("\n"):
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    return events


class WireNameTest(unittest.TestCase):
    def test_core_tools_take_claude_code_casing(self):
        self.assertEqual(provider.wire_name("bash"), "Bash")
        self.assertEqual(provider.wire_name("webfetch"), "WebFetch")
        self.assertEqual(provider.wire_name("Read"), "Read")

    def test_flat_tools_get_the_mcp_alias(self):
        self.assertEqual(provider.wire_name("todo_write"), "mcp__maki__todo_write")

    def test_mcp_tools_and_empty_names_pass_through(self):
        self.assertEqual(provider.wire_name("mcp__srv__docs"), "mcp__srv__docs")
        self.assertEqual(provider.wire_name(""), "")
        self.assertIsNone(provider.wire_name(None))

    def test_names_that_would_exceed_the_limit_stay_flat(self):
        long = "x" * (provider.NAME_LIMIT - len(provider.ALIAS_PREFIX) + 1)
        self.assertEqual(provider.wire_name(long), long)
        fits = "x" * (provider.NAME_LIMIT - len(provider.ALIAS_PREFIX))
        self.assertEqual(provider.wire_name(fits), provider.ALIAS_PREFIX + fits)

    def test_restore_name_covers_map_prefix_and_core_casing(self):
        alias_of = {"mcp__maki__batch": "batch", "Bash": "bash"}
        self.assertEqual(provider.restore_name(alias_of, "mcp__maki__batch"), "batch")
        self.assertEqual(provider.restore_name(alias_of, "Bash"), "bash")
        self.assertEqual(provider.restore_name({}, "mcp__maki__index"), "index")
        self.assertEqual(provider.restore_name({}, "Glob"), "glob")
        self.assertEqual(provider.restore_name({}, "mcp__srv__docs"), "mcp__srv__docs")


class TransformPayloadTest(unittest.TestCase):
    def test_tools_history_and_tool_choice_are_renamed_consistently(self):
        payload = {
            "tools": [
                {"name": "bash", "input_schema": {}},
                {"name": "todo_write", "input_schema": {}, "cache_control": {"type": "ephemeral"}},
                {"name": "mcp__srv__docs", "input_schema": {}},
                {"type": "web_search_20260209", "name": "web_search"},
            ],
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "todo_write", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
            ],
            "tool_choice": {"type": "tool", "name": "todo_write"},
        }
        out, alias_of = provider.transform_payload(payload)
        names = [t["name"] for t in out["tools"]]
        self.assertEqual(names, ["Bash", "mcp__maki__todo_write", "mcp__srv__docs", "web_search"])
        self.assertEqual(out["tools"][1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(out["messages"][1]["content"][0]["name"], "mcp__maki__todo_write")
        self.assertEqual(out["tool_choice"]["name"], "mcp__maki__todo_write")
        self.assertEqual(alias_of, {"Bash": "bash", "mcp__maki__todo_write": "todo_write"})

    def test_payload_without_tools_is_untouched(self):
        payload = {"messages": [{"role": "user", "content": "hi"}]}
        out, alias_of = provider.transform_payload(payload)
        self.assertEqual(out, {"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(alias_of, {})


class HeadersTest(unittest.TestCase):
    def test_betas_merge_and_client_headers_replace_makis(self):
        items = [
            ("Host", "127.0.0.1:1"),
            ("Content-Length", "3"),
            ("User-Agent", "maki/v0.5.3"),
            ("Accept-Encoding", "gzip"),
            ("Connection", "keep-alive"),
            ("Expect", "100-continue"),
            ("anthropic-beta", "fast-mode-2026-02-01, oauth-2025-04-20"),
            ("authorization", BEARER),
            ("anthropic-version", "2023-06-01"),
        ]
        out = provider.upstream_headers(items, 12)
        self.assertEqual(out["anthropic-beta"], "claude-code-20250219,oauth-2025-04-20,fast-mode-2026-02-01")
        self.assertEqual(out["user-agent"], provider.USER_AGENT)
        self.assertEqual(out["x-app"], "cli")
        self.assertEqual(out["accept-encoding"], "identity")
        self.assertEqual(out["content-length"], "12")
        self.assertEqual(out["authorization"], BEARER)
        self.assertEqual(out["anthropic-version"], "2023-06-01")
        for dropped in ("Host", "Content-Length", "Connection", "Expect", "Accept-Encoding", "User-Agent"):
            self.assertNotIn(dropped, out)


class SseRewriterTest(unittest.TestCase):
    def run_rewriter(self, alias_of, events):
        rewriter = provider.SseRewriter(alias_of)
        out = []
        for line in sse(events).splitlines(keepends=True):
            out.extend(rewriter.feed(line))
        out.extend(rewriter.finish())
        return parse_sse(b"".join(out))

    def test_tool_use_names_are_restored(self):
        events = self.run_rewriter({"mcp__maki__todo_write": "todo_write"}, UPSTREAM_EVENTS[:7])
        self.assertEqual(events[4]["content_block"]["name"], "todo_write")
        self.assertEqual(events[0], UPSTREAM_EVENTS[0])
        self.assertEqual(len(events), 7)

    def test_batch_input_is_emitted_whole_with_nested_names_restored(self):
        events = self.run_rewriter({"Glob": "glob"}, UPSTREAM_EVENTS)
        batch = [e for e in events if e.get("index") == 2]
        self.assertEqual([e["type"] for e in batch], ["content_block_start", "content_block_delta", "content_block_stop"])
        self.assertEqual(batch[0]["content_block"]["name"], "batch")
        calls = json.loads(batch[1]["delta"]["partial_json"])["tool_calls"]
        self.assertEqual([c["tool"] for c in calls], ["glob", "index"])
        self.assertEqual(calls[0]["parameters"], {"pattern": "*"})
        self.assertEqual(events[-1], {"type": "message_stop"})

    def test_batch_with_unparseable_input_falls_back_to_the_original_deltas(self):
        events = [
            UPSTREAM_EVENTS[7],
            {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": "{not json"}},
            UPSTREAM_EVENTS[10],
        ]
        out = self.run_rewriter({}, events)
        self.assertEqual([e["type"] for e in out], ["content_block_start", "content_block_delta", "content_block_stop"])
        self.assertEqual(out[1]["delta"]["partial_json"], "{not json")

    def test_non_json_and_comment_lines_pass_through(self):
        rewriter = provider.SseRewriter({})
        raw = b": ping\n\nevent: ping\ndata: not json\n\n"
        out = b"".join(b"".join(rewriter.feed(l)) for l in raw.splitlines(keepends=True))
        self.assertEqual(out, raw)


class LoginHelpersTest(unittest.TestCase):
    def test_parse_authorization_input_accepts_every_shape(self):
        url = "http://localhost:53692/callback?code=abc&state=xyz"
        self.assertEqual(provider.parse_authorization_input(url), ("abc", "xyz"))
        self.assertEqual(provider.parse_authorization_input("abc#xyz\n"), ("abc", "xyz"))
        self.assertEqual(provider.parse_authorization_input("code=abc&state=xyz"), ("abc", "xyz"))
        self.assertEqual(provider.parse_authorization_input("  abc "), ("abc", None))
        self.assertEqual(provider.parse_authorization_input("   "), (None, None))

    def test_tokens_from_response_keeps_account_and_previous_refresh(self):
        fresh = provider.tokens_from_response(
            {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "account": {"uuid": "u", "email_address": "me@example.com"}}
        )
        self.assertEqual(fresh["account_id"], "u")
        self.assertEqual(fresh["email"], "me@example.com")
        self.assertGreater(fresh["expires"], provider.now_ms())
        rotated = provider.tokens_from_response({"access_token": "b", "expires_in": 60}, fresh)
        self.assertEqual(rotated["refresh"], "r")
        self.assertEqual(rotated["email"], "me@example.com")
        with self.assertRaises(provider.Fail):
            provider.tokens_from_response({"access_token": "a"})


class PathsTest(unittest.TestCase):
    def test_paths_follow_xdg_and_the_legacy_directory(self):
        with tempfile.TemporaryDirectory() as home:
            env = {"HOME": home, "XDG_CONFIG_HOME": os.path.join(home, "cfg"), "XDG_STATE_HOME": os.path.join(home, "st")}
            with mock.patch.dict(os.environ, env, clear=False):
                config, state = provider.maki_dirs()
                self.assertEqual(config, os.path.join(home, "cfg", "maki"))
                self.assertEqual(state, os.path.join(home, "st", "maki"))
                self.assertEqual(provider.token_path(), os.path.join(state, "auth", "claude.json"))
                os.mkdir(os.path.join(home, ".maki"))
                self.assertEqual(provider.maki_dirs(), (os.path.join(home, ".maki"), os.path.join(home, ".maki")))

    def test_token_file_round_trip_is_private(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"HOME": home, "XDG_STATE_HOME": home, "XDG_CONFIG_HOME": home}):
                self.assertIsNone(provider.load_tokens())
                tokens = {"access": "a", "refresh": "r", "expires": provider.now_ms() + 10_000}
                provider.write_private_json(provider.token_path(), tokens)
                self.assertEqual(provider.load_tokens(), tokens)
                self.assertEqual(os.stat(provider.token_path()).st_mode & 0o777, 0o600)
                self.assertFalse(provider.expires_within(tokens, 5))
                self.assertTrue(provider.expires_within(tokens, 20))
                self.assertEqual(provider.auth_json(tokens, 4711)["headers"], {"authorization": "Bearer a"})
                self.assertEqual(provider.auth_json(tokens, 4711)["base_url"], "http://127.0.0.1:4711/api.anthropic.com")


class FakeUpstream(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        FakeUpstream.seen.append((self.path, dict(self.headers.items()), json.loads(body)))
        payload = sse(UPSTREAM_EVENTS)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        FakeUpstream.seen.append((self.path, dict(self.headers.items()), None))
        body = b'{"data": []}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ProxyEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream)
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.upstream_patch = mock.patch.object(provider, "UPSTREAM", "http://127.0.0.1:%d" % cls.upstream.server_address[1])
        cls.upstream_patch.start()
        cls.proxy = provider.build_server(0)
        threading.Thread(target=cls.proxy.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()
        cls.upstream.shutdown()
        cls.upstream_patch.stop()

    def setUp(self):
        FakeUpstream.seen.clear()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.proxy.server_address[1], timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        return response, raw

    def test_health(self):
        response, raw = self.request("GET", provider.HEALTH_PATH)
        self.assertEqual((response.status, raw), (200, b"ok"))
        self.assertEqual(FakeUpstream.seen, [])

    def test_messages_are_rewritten_both_ways(self):
        payload = {
            "model": "claude-opus-5",
            "stream": True,
            "system": [{"type": "text", "text": provider.IDENTITY}, {"type": "text", "text": "maki"}],
            "tools": [{"name": "glob", "input_schema": {}}, {"name": "todo_write", "input_schema": {}}, {"name": "batch", "input_schema": {}}],
            "messages": [{"role": "user", "content": "go"}],
        }
        response, raw = self.request(
            "POST",
            provider.UPSTREAM_MARKER + provider.MESSAGES_PATH,
            body=json.dumps(payload),
            headers={"content-type": "application/json", "authorization": BEARER, "anthropic-beta": "fast-mode-2026-02-01", "user-agent": "maki/test"},
        )
        self.assertEqual(response.status, 200)
        self.assertIn("text/event-stream", response.getheader("content-type"))

        path, headers, body = FakeUpstream.seen[0]
        self.assertEqual(path, provider.MESSAGES_PATH)
        self.assertEqual([t["name"] for t in body["tools"]], ["Glob", "mcp__maki__todo_write", "mcp__maki__batch"])
        self.assertEqual(body["system"][0]["text"], provider.IDENTITY)
        self.assertEqual(headers["anthropic-beta"], "claude-code-20250219,oauth-2025-04-20,fast-mode-2026-02-01")
        self.assertEqual(headers["user-agent"], provider.USER_AGENT)
        self.assertEqual(headers["x-app"], "cli")
        self.assertEqual(headers["authorization"], BEARER)

        events = parse_sse(raw)
        tool_uses = [e["content_block"]["name"] for e in events if e.get("type") == "content_block_start" and e["content_block"]["type"] == "tool_use"]
        self.assertEqual(tool_uses, ["todo_write", "batch"])
        batch_deltas = [e for e in events if e.get("type") == "content_block_delta" and e.get("index") == 2]
        self.assertEqual(len(batch_deltas), 1)
        calls = json.loads(batch_deltas[0]["delta"]["partial_json"])["tool_calls"]
        self.assertEqual([c["tool"] for c in calls], ["glob", "index"])
        self.assertEqual(events[-1], {"type": "message_stop"})

    def test_other_paths_relay_unchanged(self):
        response, raw = self.request("GET", "/v1/models?limit=1", headers={"authorization": BEARER})
        self.assertEqual((response.status, json.loads(raw)), (200, {"data": []}))
        path, headers, _ = FakeUpstream.seen[0]
        self.assertEqual(path, "/v1/models?limit=1")
        self.assertEqual(headers["authorization"], BEARER)
        self.assertEqual(headers["anthropic-beta"], ",".join(provider.OAUTH_BETAS))


if __name__ == "__main__":
    unittest.main()
