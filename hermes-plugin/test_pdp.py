"""Lightweight tests for the Hermes Python PDP (stdlib unittest)."""

from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from datetime import datetime, timedelta, timezone

from pdp import GovernancePdp, _canonical_json, resolve_tool_policy, verify_payload_hs256


SECRET = "test-secret"


def sign(payload: dict) -> str:
    return hmac.new(
        SECRET.encode("utf-8"),
        _canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class PdpTests(unittest.TestCase):
    def test_verify(self) -> None:
        payload = {"a": 1, "b": "x"}
        sig = sign(payload)
        self.assertTrue(verify_payload_hs256(payload, sig, SECRET))
        self.assertFalse(verify_payload_hs256({"a": 2}, sig, SECRET))

    def test_resolve_category(self) -> None:
        payload = {
            "tools": {},
            "tool_categories": {
                "calendar.read": {
                    "tier": "velocity",
                    "requires_approval": False,
                    "granted": True,
                }
            },
        }
        pol = resolve_tool_policy(payload, "calendar.get_events")
        self.assertIsNotNone(pol)
        assert pol is not None
        self.assertTrue(pol["granted"])

    def test_evaluate_allow_deny(self) -> None:
        now = datetime.now(timezone.utc)
        payload = {
            "org_id": "o",
            "agent_key": "penn",
            "environment": "production",
            "version": "v1",
            "etag": "e",
            "issued_at": now.isoformat(),
            "not_before": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "grace_ms": 3600000,
            "default_tier": "efficiency",
            "tools": {
                "calendar.get_events": {
                    "tier": "velocity",
                    "requires_approval": False,
                    "granted": True,
                }
            },
        }
        pdp = GovernancePdp(
            base_url="https://example.test",
            token="t",
            verify_key=SECRET,
            agent_key="penn",
        )
        pdp.bundle = {
            "alg": "HS256",
            "signature": sign(payload),
            "payload": payload,
        }
        allow = pdp.evaluate("calendar.get_events")
        self.assertEqual(allow["decision"], "allow")
        deny = pdp.evaluate("email.send")
        self.assertEqual(deny["decision"], "deny")


if __name__ == "__main__":
    unittest.main()
