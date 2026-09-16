"""
Tests for the paths where a failure is a security failure, not a bug.

These cover `refresh()` and `_derive_state()` end to end rather than the primitives
underneath them. The distinction matters: `test_pdp.py` proves `verify_payload_hs256`
rejects a bad signature in isolation, then installs a bundle by assigning `pdp.bundle`
directly — so the branch in `refresh()` that actually calls the verifier, and the branch
that refuses a non-HS256 algorithm, were never executed by any test. A regression there
would have been silent, and it would have accepted forged policy.

Every test here asserts one of four properties:

  * a bundle that does not verify is refused, and does not become the active policy
  * an algorithm we do not implement is refused rather than trusted
  * an unreachable server with nothing cached denies, and never allows
  * expiry and grace are honoured at the boundary, so a stale bundle stops governing

The server is faked at `_fetch_json`, which is the narrowest seam that still exercises
all of `refresh()`. Nothing here touches the network.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import unittest
from datetime import datetime, timedelta, timezone

from pdp import NOT_MODIFIED, GovernancePdp, PdpError, _canonical_json

SECRET = "test-secret"


def sign(payload: dict, secret: str = SECRET) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        _canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def iso(when: datetime) -> str:
    return when.isoformat().replace("+00:00", "Z")


def payload(*, expires_in_s: int = 3600, grace_ms: int = 0, granted: bool = True) -> dict:
    return {
        "version": "2026.01.01.testbundle",
        "agent_key": "my-agent",
        "environment": "production",
        "expires_at": iso(datetime.now(timezone.utc) + timedelta(seconds=expires_in_s)),
        "grace_ms": grace_ms,
        "default_tier": "efficiency",
        "tools": {
            "calendar.get_events": {
                "granted": granted,
                "tier": "velocity",
                "requires_approval": False,
            }
        },
        "tool_categories": {},
    }


def bundle(pl: dict, *, alg: str = "HS256", secret: str = SECRET) -> dict:
    return {"alg": alg, "payload": pl, "signature": sign(pl, secret)}


def make_pdp(responses: dict) -> GovernancePdp:
    """A PDP whose server returns `responses` keyed by URL substring.

    A value that is an Exception is raised, which is how an unreachable server and an
    HTTP error both present to `refresh()`.
    """
    pdp = GovernancePdp(
        base_url="https://example.invalid",
        token="pf_svc_test",
        verify_key=SECRET,
        agent_key="my-agent",
    )

    def fake_fetch(path: str, allow_conditional: bool = True):  # noqa: ARG001
        for needle, value in responses.items():
            if needle in path:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected fetch: {path}")

    pdp._fetch_json = fake_fetch  # type: ignore[method-assign]
    return pdp


class SignatureRejection(unittest.TestCase):
    def test_tampered_payload_is_refused_and_does_not_become_policy(self) -> None:
        good = payload()
        b = bundle(good)
        # Signature stays valid for the original; the payload is swapped underneath it,
        # which is exactly the shape of a bundle edited in transit or on disk.
        b["payload"] = {**good, "tools": {"calendar.get_events": {"granted": True,
                                                                  "tier": "velocity",
                                                                  "requires_approval": False},
                                          "contacts.delete": {"granted": True,
                                                              "tier": "velocity",
                                                              "requires_approval": False}}}
        pdp = make_pdp({"/packs/": {"version": 1}, "/bundles/": b})

        with self.assertRaises(PdpError) as ctx:
            pdp.refresh()
        self.assertIn("signature", str(ctx.exception).lower())
        # The critical assertion is not that it raised — it is that the forged grant
        # never became live. A raise that still installed the bundle would be worse
        # than no check at all, because the error is recoverable and the grant is not.
        self.assertIsNone(pdp.bundle)
        self.assertEqual(pdp.evaluate("contacts.delete")["decision"], "deny")

    def test_signature_from_a_different_key_is_refused(self) -> None:
        pl = payload()
        pdp = make_pdp(
            {"/packs/": {"version": 1}, "/bundles/": bundle(pl, secret="not-our-secret")}
        )
        with self.assertRaises(PdpError):
            pdp.refresh()
        self.assertIsNone(pdp.bundle)

    def test_unsupported_alg_is_refused_rather_than_trusted(self) -> None:
        # "none" is the classic downgrade: if the alg field were honoured without an
        # allowlist, an attacker would simply declare the signature irrelevant.
        for alg in ("none", "HS512", "RS256", ""):
            with self.subTest(alg=alg):
                pl = payload()
                pdp = make_pdp({"/packs/": {"version": 1}, "/bundles/": bundle(pl, alg=alg)})
                with self.assertRaises(PdpError) as ctx:
                    pdp.refresh()
                self.assertIn("alg", str(ctx.exception).lower())
                self.assertIsNone(pdp.bundle)


class FailClosedWhenUnreachable(unittest.TestCase):
    def test_unreachable_server_with_no_cached_bundle_denies_everything(self) -> None:
        pdp = make_pdp({"/packs/": OSError("connection refused"),
                        "/bundles/": OSError("connection refused")})

        with self.assertRaises(OSError):
            pdp.refresh()

        self.assertEqual(pdp._derive_state(), "fail_closed")
        # Including an act that would be granted by a healthy bundle: unavailability
        # must not widen access.
        for act in ("calendar.get_events", "anything.at.all"):
            self.assertEqual(pdp.evaluate(act)["decision"], "deny")

    def test_unreachable_server_keeps_serving_a_still_valid_cached_bundle(self) -> None:
        pdp = make_pdp({"/packs/": {"version": 1}, "/bundles/": bundle(payload())})
        pdp.refresh()
        self.assertEqual(pdp.evaluate("calendar.get_events")["decision"], "allow")

        pdp._fetch_json = lambda path, allow_conditional=True: (_ for _ in ()).throw(  # type: ignore[method-assign]
            OSError("connection refused")
        )
        result = pdp.refresh()

        # Degrades to "cached" rather than raising: an outage should not stop an agent
        # doing work its policy already permits, for as long as that policy is current.
        self.assertEqual(result["state"], "cached")
        self.assertEqual(pdp.evaluate("calendar.get_events")["decision"], "allow")


class ExpiryAndGraceBoundaries(unittest.TestCase):
    def _loaded(self, **kw) -> GovernancePdp:
        pl = payload(**kw)
        pdp = make_pdp({"/packs/": {"version": 1}, "/bundles/": bundle(pl)})
        pdp.refresh()
        return pdp

    def test_state_is_normal_while_current(self) -> None:
        self.assertEqual(self._loaded(expires_in_s=3600)._derive_state(), "normal")

    def test_expired_with_no_grace_fails_closed(self) -> None:
        pdp = self._loaded(expires_in_s=-1, grace_ms=0)
        self.assertEqual(pdp._derive_state(), "fail_closed")
        self.assertEqual(pdp.evaluate("calendar.get_events")["decision"], "deny")

    def test_expired_inside_grace_still_governs(self) -> None:
        # Expired 1s ago with a 60s grace: the bundle is stale but deliberately honoured,
        # so a brief outage at the wrong moment does not halt a fleet.
        pdp = self._loaded(expires_in_s=-1, grace_ms=60_000)
        self.assertEqual(pdp._derive_state(), "grace")
        self.assertEqual(pdp.evaluate("calendar.get_events")["decision"], "allow")

    def test_expired_past_grace_fails_closed(self) -> None:
        pdp = self._loaded(expires_in_s=-120, grace_ms=60_000)
        self.assertEqual(pdp._derive_state(), "fail_closed")
        self.assertEqual(pdp.evaluate("calendar.get_events")["decision"], "deny")

    def test_unparseable_expiry_fails_closed(self) -> None:
        # A malformed timestamp must not read as "no expiry". Treating it as valid
        # forever is the single worst way to misread this field.
        pdp = self._loaded()
        pdp.bundle["payload"]["expires_at"] = "not-a-timestamp"  # type: ignore[index]
        self.assertEqual(pdp._derive_state(), "fail_closed")
        self.assertEqual(pdp.evaluate("calendar.get_events")["decision"], "deny")

    def test_missing_expiry_fails_closed(self) -> None:
        pdp = self._loaded()
        del pdp.bundle["payload"]["expires_at"]  # type: ignore[index]
        self.assertEqual(pdp._derive_state(), "fail_closed")


class NotModifiedPath(unittest.TestCase):
    def test_304_keeps_the_verified_bundle_without_reverifying(self) -> None:
        pdp = make_pdp({"/packs/": {"version": 1}, "/bundles/": bundle(payload())})
        first = pdp.refresh()
        self.assertNotIn("not_modified", first)

        pdp._fetch_json = lambda path, allow_conditional=True: NOT_MODIFIED  # type: ignore[method-assign]
        second = pdp.refresh()

        self.assertTrue(second["not_modified"])
        self.assertEqual(second["bundle_version"], first["bundle_version"])
        self.assertEqual(pdp.evaluate("calendar.get_events")["decision"], "allow")

    def test_a_304_cannot_introduce_an_unverified_bundle(self) -> None:
        # Guards the ordering in refresh(): the not-modified short-circuit must come
        # after a bundle exists, never as a way to skip verification on first load.
        pdp = make_pdp({"/packs/": {"version": 1}, "/bundles/": NOT_MODIFIED})
        try:
            pdp.refresh()
        except Exception:  # noqa: BLE001 — either raising or denying is acceptable
            pass
        self.assertEqual(pdp.evaluate("calendar.get_events")["decision"], "deny")


if __name__ == "__main__":
    unittest.main()
