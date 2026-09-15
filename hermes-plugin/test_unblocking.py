"""Tests for the unblocking slice: conditional revalidation, renewal safety, and
in-channel approval eligibility.

See docs/prds/agent-unblocking-initiative.md in the PromptForge repo.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import pdp as pdp_mod
from pdp import GovernancePdp, RENEW_MARGIN_S


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _pdp_with_expiry(expires_at: str) -> GovernancePdp:
    p = GovernancePdp(
        base_url="https://example.test",
        token="t",
        verify_key="k",
        agent_key="alex",
    )
    p.bundle = {"payload": {"expires_at": expires_at, "version": "v1"}}
    return p


class CategoryKeyTests(unittest.TestCase):
    def test_write_acts_resolve_to_write_category(self):
        self.assertEqual(pdp_mod.category_key_for("email.send_now"), "email.write")

    def test_read_prefixes_resolve_to_read_category(self):
        for name in ("email.get_thread", "email.list_folders", "email.search"):
            self.assertEqual(pdp_mod.category_key_for(name), "email.read")

    def test_multi_segment_uses_second_segment(self):
        # Matches evaluate.ts, which destructures split('.') rather than split('.', 1).
        self.assertEqual(
            pdp_mod.category_key_for("analytics.search.daily"), "analytics.read"
        )

    def test_domainless_act_has_no_category(self):
        # some act names have no dot; they must not be swept into a category grant.
        self.assertIsNone(pdp_mod.category_key_for("hermes_native_act"))


class InlineApprovalEligibilityTests(unittest.TestCase):
    def test_absent_field_is_off(self):
        # Default-off is the whole safety property: no agent gains in-channel
        # approval until PromptForge deliberately publishes it.
        self.assertFalse(pdp_mod.inline_approval_enabled({}))

    def test_false_is_off(self):
        self.assertFalse(pdp_mod.inline_approval_enabled({"inline_approval": False}))

    def test_only_boolean_true_enables(self):
        self.assertTrue(pdp_mod.inline_approval_enabled({"inline_approval": True}))

    def test_truthy_non_boolean_does_not_enable(self):
        # A stray string must not silently widen who can approve.
        for value in ("true", "yes", 1, [1]):
            self.assertFalse(
                pdp_mod.inline_approval_enabled({"inline_approval": value}),
                f"{value!r} must not enable inline approval",
            )


class BundleRenewalTests(unittest.TestCase):
    """The outage guard.

    The bundle ETag covers the policy hash only, while expires_at is recomputed as
    now + ttl per request. Always sending If-None-Match would therefore pin a gateway
    to its original expiry and fail the whole fleet closed against a healthy server.
    """

    def test_no_bundle_needs_full_copy(self):
        p = GovernancePdp(
            base_url="https://example.test", token="t", verify_key="k", agent_key="alex"
        )
        self.assertTrue(p._bundle_needs_renewal())

    def test_fresh_bundle_may_revalidate(self):
        expires = datetime.now(timezone.utc) + timedelta(seconds=RENEW_MARGIN_S + 600)
        self.assertFalse(_pdp_with_expiry(_iso(expires))._bundle_needs_renewal())

    def test_bundle_near_expiry_forces_full_copy(self):
        expires = datetime.now(timezone.utc) + timedelta(seconds=RENEW_MARGIN_S - 60)
        self.assertTrue(_pdp_with_expiry(_iso(expires))._bundle_needs_renewal())

    def test_already_expired_bundle_forces_full_copy(self):
        expires = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertTrue(_pdp_with_expiry(_iso(expires))._bundle_needs_renewal())

    def test_unparseable_expiry_forces_full_copy(self):
        self.assertTrue(_pdp_with_expiry("not-a-date")._bundle_needs_renewal())


class ConditionalRequestTests(unittest.TestCase):
    def test_no_etag_cached_sends_no_conditional_header(self):
        p = GovernancePdp(
            base_url="https://example.test", token="t", verify_key="k", agent_key="alex"
        )
        captured = {}

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["headers"] = dict(req.headers)
            raise AssertionError("stop before I/O")

        import urllib.request

        original = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(Exception):
                p._fetch_json("/api/governance/bundles/alex")
        finally:
            urllib.request.urlopen = original
        self.assertNotIn("If-none-match", captured["headers"])

    def test_cached_etag_is_sent_and_suppressed_when_disallowed(self):
        p = GovernancePdp(
            base_url="https://example.test", token="t", verify_key="k", agent_key="alex"
        )
        path = "/api/governance/bundles/alex"
        p._etags[path] = 'W/"bundle-alex-production-v1"'
        captured = {}

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["headers"] = dict(req.headers)
            raise AssertionError("stop before I/O")

        import urllib.request

        original = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(Exception):
                p._fetch_json(path)
            self.assertEqual(
                captured["headers"].get("If-none-match"),
                'W/"bundle-alex-production-v1"',
            )
            with self.assertRaises(Exception):
                p._fetch_json(path, allow_conditional=False)
            self.assertNotIn("If-none-match", captured["headers"])
        finally:
            urllib.request.urlopen = original


if __name__ == "__main__":
    unittest.main()
