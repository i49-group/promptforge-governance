"""Tests for reporting the host's own standing permissions to PromptForge.

The defect this closes: an "always" approval given in a chat became a permanent grant in the
host's config, outside the signed policy, where PromptForge could not see it. These tests pin
the three things the report must get right: it never carries a secret off the host, it changes
only when the grants change, and it can never delay or fail the agent it reports on.
"""

from __future__ import annotations

import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hp = _load("pf_host_permissions_under_test", "host_permissions.py")


def _no_redact(text: str) -> str:
    return text


CONFIG = {
    "approvals": {
        "mode": "smart",
        "cron_mode": "deny",
        "deny": ["rm -rf /*"],
    },
    "command_allowlist": [
        "script execution via -e/-c flag",
        "/usr/bin/python3",
        "=command:3f2a9c",
        "plugin_rule:example-plugin:network",
        "script execution via -e/-c flag",
        "",
        42,
    ],
}


class SnapshotTest(unittest.TestCase):
    def test_classifies_and_deduplicates(self):
        snap = hp.build_snapshot(CONFIG, _no_redact)
        self.assertEqual(snap["approval_mode"], "smart")
        self.assertEqual(snap["unattended_modes"], {"cron_mode": "deny"})
        self.assertEqual(snap["deny_rules"], ["rm -rf /*"])
        self.assertEqual(
            sorted((e["kind"], e["key"]) for e in snap["permanent_approvals"]),
            [
                ("binary", "/usr/bin/python3"),
                ("command_hash", "=command:3f2a9c"),
                ("pattern", "script execution via -e/-c flag"),
                ("rule", "plugin_rule:example-plugin:network"),
            ],
        )

    def test_missing_or_malformed_config_is_an_empty_grant_not_an_error(self):
        snap = hp.build_snapshot({"approvals": "nonsense", "command_allowlist": "also nonsense"}, _no_redact)
        self.assertEqual(snap["permanent_approvals"], [])
        self.assertIsNone(snap["approval_mode"])

    def test_token_shaped_entries_never_leave_in_clear(self):
        secret = "ghp_" + "A1b2C3d4" * 5
        snap = hp.build_snapshot(
            {"command_allowlist": [f"curl -H 'Authorization: Bearer {secret}'"], "approvals": {"deny": [secret]}},
            _no_redact,
        )
        body = json.dumps(snap)
        self.assertNotIn(secret, body)
        self.assertNotIn("Bearer", body)
        self.assertTrue(snap["permanent_approvals"][0]["key"].startswith("sha256:"))
        self.assertTrue(snap["deny_rules"][0].startswith("sha256:"))

    def test_hermes_redaction_also_decides(self):
        snap = hp.build_snapshot(
            {"command_allowlist": ["export HOST_SECRET=hunter2"]},
            lambda t: t.replace("hunter2", "***"),
        )
        self.assertTrue(snap["permanent_approvals"][0]["key"].startswith("sha256:"))

    def test_ordinary_names_stay_readable(self):
        snap = hp.build_snapshot(
            {"command_allowlist": ["disk-usage", "/opt/tools/interpreters/cpython/versions/current/bin/python3"]},
            _no_redact,
        )
        keys = {e["key"] for e in snap["permanent_approvals"]}
        self.assertIn("disk-usage", keys)
        self.assertIn("/opt/tools/interpreters/cpython/versions/current/bin/python3", keys)

    def test_digest_ignores_order_and_moves_on_change(self):
        a = hp.build_snapshot(CONFIG, _no_redact)
        reordered = dict(CONFIG, command_allowlist=list(reversed(CONFIG["command_allowlist"])))
        self.assertEqual(hp.digest(a), hp.digest(hp.build_snapshot(reordered, _no_redact)))
        widened = dict(CONFIG, command_allowlist=CONFIG["command_allowlist"] + ["/bin/bash"])
        self.assertNotEqual(hp.digest(a), hp.digest(hp.build_snapshot(widened, _no_redact)))


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _reporter(config, clock, **kw):
    return hp.HostPermissionsReporter(
        base_url="https://pf.test/",
        token="tok",
        agent_key="ops-agent",
        load_config=lambda: config,
        redact=_no_redact,
        clock=clock,
        **kw,
    )


class ReporterTest(unittest.TestCase):
    def test_sends_once_then_only_on_change_or_resend(self):
        now = [1000.0]
        config = dict(CONFIG)
        r = _reporter(config, lambda: now[0], resend_s=3600)
        bodies = []

        def fake_urlopen(req, timeout):
            bodies.append(json.loads(req.data))
            self.assertEqual(req.full_url, "https://pf.test/api/governance/host-permissions")
            return _Resp(b'{"success":true,"data":{"accepted":true}}')

        with mock.patch.object(hp.urllib.request, "urlopen", fake_urlopen):
            self.assertTrue(r.check())
            r.drain()
            self.assertFalse(r.check())
            config["command_allowlist"] = CONFIG["command_allowlist"] + ["/bin/bash"]
            self.assertTrue(r.check())
            r.drain()
            self.assertFalse(r.check())
            now[0] += 3601
            self.assertTrue(r.check())
            r.drain()

        self.assertEqual(len(bodies), 3)
        self.assertEqual(bodies[0]["schema"], 1)
        self.assertEqual(bodies[0]["harness"], "hermes")
        self.assertEqual(bodies[0]["agent_key"], "ops-agent")
        self.assertTrue(bodies[0]["observed_at"].endswith("Z"))
        self.assertEqual(r.sent, 3)

    def test_rejected_report_is_retried_on_the_next_check(self):
        r = _reporter(dict(CONFIG), lambda: 0.0)
        with mock.patch.object(
            hp.urllib.request, "urlopen", lambda req, timeout: _Resp(b'{"data":{"accepted":false}}')
        ):
            r.check()
            r.drain()
            self.assertEqual(r.rejected, 1)
            self.assertTrue(r.check())
            r.drain()

    def test_failures_never_raise(self):
        def boom(req, timeout):
            raise OSError("network down")

        r = _reporter(dict(CONFIG), lambda: 0.0)
        with mock.patch.object(hp.urllib.request, "urlopen", boom):
            self.assertTrue(r.check())
            r.drain()
        self.assertEqual(r.failed, 1)

        def bad_config():
            raise RuntimeError("config unreadable")

        broken = hp.HostPermissionsReporter(
            base_url="https://pf.test", token="tok", agent_key="ops-agent", load_config=bad_config
        )
        self.assertFalse(broken.check())

    def test_check_does_not_wait_for_the_network(self):
        import threading
        import time

        release = threading.Event()

        def slow(req, timeout):
            release.wait(2)
            return _Resp(b'{"data":{"accepted":true}}')

        r = _reporter(dict(CONFIG), lambda: 0.0)
        with mock.patch.object(hp.urllib.request, "urlopen", slow):
            started = time.monotonic()
            r.check()
            self.assertLess(time.monotonic() - started, 0.5)
            release.set()
            r.drain()


if __name__ == "__main__":
    unittest.main()
