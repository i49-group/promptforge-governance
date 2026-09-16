"""
Minimal local PDP for Hermes (Python).

Mirrors @promptforge/governance-pdp semantics:
  refresh pack + signed bundle → verify HS256 → cache → evaluate → fail-closed
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4


class PdpError(Exception):
    pass


TIER_RANK = {"velocity": 1, "efficiency": 2, "control": 3}

# Sentinel for a 304: the server confirmed our cached copy is current.
NOT_MODIFIED = object()

# Take a full bundle copy once expiry is closer than this, so a stable ETag can
# never strand us on an expiring bundle. See _bundle_needs_renewal.
RENEW_MARGIN_S = 900


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def _b64url(digest: bytes) -> str:
    """Match Node crypto HMAC digest('base64url') — no padding."""
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def verify_payload_hs256(payload: dict, signature: str, secret: str) -> bool:
    digest = hmac.new(
        secret.encode("utf-8"),
        _canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected = _b64url(digest)
    if len(expected) != len(signature):
        return False
    return hmac.compare_digest(expected, signature)


def _parse_iso(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def _max_tier(a: Optional[str], b: Optional[str]) -> str:
    """Most-restrictive-wins, matching maxTier() in evaluate.ts."""
    left = a or "efficiency"
    right = b or "efficiency"
    return left if TIER_RANK.get(left, 2) >= TIER_RANK.get(right, 2) else right


def evaluate_against_bundle(
    payload: dict,
    tool_name: str,
    pdp_state: str = "normal",
    correlation_id: Optional[str] = None,
) -> dict:
    """Evaluate a tool request against an already-verified bundle payload.

    Line-for-line equivalent of evaluateAgainstBundle() in
    packages/governance-pdp/src/evaluate.ts, which is the reference
    implementation. Equivalence is proven — not assumed — by
    conformance/vectors.json, which this and the TypeScript evaluator both run.
    Do not change behaviour here without changing the reference and the vectors
    together.
    """
    corr = correlation_id or str(uuid4())
    version = payload.get("version") or "none"

    if pdp_state == "fail_closed":
        return {
            "decision": "deny",
            "tier": "control",
            "requires_approval": True,
            "reasons": ["pdp_fail_closed"],
            "bundle_version": version,
            "correlation_id": corr,
            "pdp_state": pdp_state,
        }

    tool_policy = resolve_tool_policy(payload, tool_name)
    if not tool_policy:
        return {
            "decision": "deny",
            "tier": "control",
            "requires_approval": True,
            "reasons": ["unknown_tool", f"tool:{tool_name}"],
            "bundle_version": version,
            "correlation_id": corr,
            "pdp_state": pdp_state,
        }

    if not tool_policy.get("granted", False):
        return {
            "decision": "deny",
            # "or control" is defensive only; a bundle whose policy omits tier is
            # malformed and outside the vectors.
            "tier": tool_policy.get("tier") or "control",
            "requires_approval": True,
            "reasons": ["not_granted", f"tool:{tool_name}"],
            "bundle_version": version,
            "correlation_id": corr,
            "pdp_state": pdp_state,
        }

    tier = _max_tier(payload.get("default_tier"), tool_policy.get("tier"))
    # The control tier gates on its own, independent of the per-act flag. Omitting
    # this is what let Hermes allow control-tier acts outright while PromptForge
    # and your ops platform gated them (see the 09-15-2026 governance review).
    requires_approval = bool(tool_policy.get("requires_approval")) or tier == "control"

    reasons = [f"tier:{tier}"]
    if pdp_state == "grace":
        reasons.append("grace_period")
    if pdp_state == "cached":
        reasons.append("using_cached_bundle")
    if requires_approval:
        reasons.append("requires_approval")

    return {
        "decision": "require_approval" if requires_approval else "allow",
        "tier": tier,
        "requires_approval": requires_approval,
        "reasons": reasons,
        "bundle_version": version,
        "correlation_id": corr,
        "pdp_state": pdp_state,
    }


def category_key_for(tool_name: str) -> Optional[str]:
    """The `{domain}.{read|write}` category an act falls under, or None if the name
    has no domain/action shape. Kept separate from resolve_tool_policy so the
    evaluator's return contract stays byte-identical to evaluate.ts (the
    conformance vectors compare both languages against the same shape)."""
    parts = tool_name.split(".")
    domain = parts[0]
    action = parts[1] if len(parts) > 1 else ""
    if not domain or not action:
        return None
    is_read = (
        action.startswith("get_")
        or action.startswith("list_")
        or action == "search"
    )
    return f"{domain}.{'read' if is_read else 'write'}"


def inline_approval_enabled(payload: dict) -> bool:
    """Whether PromptForge has marked this agent eligible for in-channel approval.

    Defaults to False, deliberately. Hermes records no approver identity and has no
    role concept, so its gate asks whoever is present — enabling this globally would
    silently reduce "only a named admin may approve" (Glen, 09-15-2026) to "anyone
    in the channel". Eligibility is therefore opt-in per agent, published by
    PromptForge, and only for agents whose channel is already admin-only.
    """
    return bool(payload.get("inline_approval") is True)


def report_decisions_enabled(payload: dict) -> bool:
    """Whether this agent's policy asks the host to report its local decisions.

    Defaults to False. Reporting sends act names off the host, so it is opt-in per
    agent rather than a global default; and because the flag lives in the bundle,
    an agent with no bundle reports nothing at all.
    """
    return bool(payload.get("report_decisions") is True)


def resolve_tool_policy(payload: dict, tool_name: str) -> Optional[dict]:
    tools = payload.get("tools") or {}
    if tool_name in tools:
        return tools[tool_name]

    # Split on every dot and take the second segment, matching
    # `const [domain, action] = toolName.split('.')` in evaluate.ts. Splitting
    # once and keeping the remainder differs for names like analytics.search.daily,
    # where the remainder is not equal to "search" but the second segment is.
    category_key = category_key_for(tool_name)
    if not category_key:
        return None
    cats = payload.get("tool_categories") or {}
    return cats.get(category_key)


class GovernancePdp:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        verify_key: str,
        agent_key: str,
        environment: str = "production",
        timeout_s: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.verify_key = verify_key
        self.agent_key = agent_key.lower()
        self.environment = (
            "staging" if environment == "staging" else "production"
        )
        self.timeout_s = timeout_s
        self.pack: Optional[dict] = None
        self.bundle: Optional[dict] = None
        self._last_refresh_failed = False
        self._etags: dict = {}

    @property
    def bundle_version(self) -> str:
        if not self.bundle:
            return "none"
        return (self.bundle.get("payload") or {}).get("version") or "none"

    @property
    def state(self) -> str:
        return self._derive_state()

    def _bundle_needs_renewal(self) -> bool:
        """True when the cached bundle is close enough to expiry that we must take a
        full copy rather than revalidate.

        The bundle ETag is derived from the policy hash alone
        (`bundle-{agent}-{env}-{version}`), while `expires_at` is recomputed as
        `now + ttl` on every request. So an unchanged policy revalidates as 304
        indefinitely, and a client that always sends If-None-Match would keep a
        bundle frozen at its original expiry, slide into grace, and then fail closed
        fleet-wide against a perfectly healthy server. Renewing early is what makes
        conditional revalidation safe here.
        """
        if not self.bundle:
            return True
        payload = self.bundle.get("payload") or {}
        try:
            expires = _parse_iso(payload["expires_at"])
        except Exception:  # noqa: BLE001
            return True
        remaining = expires.timestamp() - datetime.now(timezone.utc).timestamp()
        return remaining < RENEW_MARGIN_S

    def _fetch_json(self, path: str, *, allow_conditional: bool = True) -> Any:
        """GET with conditional revalidation. Returns NOT_MODIFIED when the server
        confirms our copy is current, which makes a refresh cheap enough to run on
        every denial instead of only on a timer."""
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        cached_etag = self._etags.get(path) if allow_conditional else None
        if cached_etag:
            headers["If-None-Match"] = cached_etag
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                etag = resp.headers.get("ETag")
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # urllib raises on 304 because it is not a 2xx. It is the success case.
            if exc.code == 304:
                return NOT_MODIFIED
            detail = exc.read().decode("utf-8", errors="replace")
            raise PdpError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
        except Exception as exc:  # noqa: BLE001
            raise PdpError(str(exc)) from exc

        if not body.get("success") or "data" not in body:
            raise PdpError(body.get("error") or "Unsuccessful governance response")
        if etag:
            self._etags[path] = etag
        return body["data"]

    def refresh(self) -> dict:
        qs = f"environment={self.environment}"
        key = urllib.parse.quote(self.agent_key, safe="")
        try:
            pack = self._fetch_json(f"/api/governance/packs/{key}?{qs}")
            bundle = self._fetch_json(
                f"/api/governance/bundles/{key}?{qs}",
                allow_conditional=not self._bundle_needs_renewal(),
            )

            if pack is NOT_MODIFIED:
                pack = self.pack
            if bundle is NOT_MODIFIED:
                # Nothing changed server-side; keep the verified copy rather than
                # re-verifying a signature we already checked.
                self._last_refresh_failed = False
                return {
                    "pack_version": (pack or {}).get("version"),
                    "bundle_version": self.bundle_version,
                    "state": self._derive_state(),
                    "not_modified": True,
                }

            if bundle.get("alg") != "HS256":
                raise PdpError(f"Unsupported bundle alg: {bundle.get('alg')}")

            payload = bundle.get("payload") or {}
            sig = bundle.get("signature") or ""
            if not verify_payload_hs256(payload, sig, self.verify_key):
                raise PdpError("Bundle signature verification failed")

            self.pack = pack
            self.bundle = bundle
            self._last_refresh_failed = False
            return {
                "pack_version": pack.get("version"),
                "bundle_version": payload.get("version"),
                "state": self._derive_state(),
            }
        except Exception:
            self._last_refresh_failed = True
            if self.bundle is None:
                raise
            return {
                "pack_version": (self.pack or {}).get("version") or "unknown",
                "bundle_version": self.bundle_version,
                "state": self._derive_state(),
            }

    def _derive_state(self) -> str:
        if not self.bundle:
            return "fail_closed"
        payload = self.bundle.get("payload") or {}
        try:
            expires = _parse_iso(payload["expires_at"])
        except Exception:  # noqa: BLE001
            return "fail_closed"
        now = datetime.now(timezone.utc)
        if now < expires:
            return "cached" if self._last_refresh_failed else "normal"
        grace_ms = int(payload.get("grace_ms") or 0)
        grace_end = expires.timestamp() + (grace_ms / 1000.0)
        if now.timestamp() < grace_end:
            return "grace"
        return "fail_closed"

    def evaluate(self, tool_name: str, correlation_id: Optional[str] = None) -> dict:
        corr = correlation_id or str(uuid4())
        state = self._derive_state()
        if not self.bundle:
            return {
                "decision": "deny",
                "tier": "control",
                "requires_approval": True,
                "reasons": ["no_bundle_loaded", "pdp_fail_closed"],
                "bundle_version": "none",
                "correlation_id": corr,
                "pdp_state": "fail_closed",
            }

        return evaluate_against_bundle(
            self.bundle.get("payload") or {},
            tool_name,
            pdp_state=state,
            correlation_id=corr,
        )

    def talk_system_prompt(self) -> Optional[str]:
        if not self.pack:
            return None
        order = ["soul", "principles", "operational", "knowledge"]
        by_type = {
            c.get("context_type"): (c.get("content") or "").strip()
            for c in (self.pack.get("contexts") or [])
        }
        parts = [by_type[t] for t in order if by_type.get(t)]
        if not parts:
            return None
        return "\n\n---\n\n".join(parts)
