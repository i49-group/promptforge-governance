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

# Both forms are needed and neither is optional: hosts load this directory as a package
# (relative import works) and as a flat path on sys.path (absolute works). A bare absolute
# import here fails under package loading, and __init__'s own fallback then masks it as
# "No module named 'pdp'" — the plugin does not load and the agent is silently ungoverned.
try:
    from .actname import resolution_candidates
    from .derive import candidate_acts, derive_facets, dispatched_call
except ImportError:  # loaded as flat plugin directory on sys.path
    from actname import resolution_candidates  # type: ignore
    from derive import candidate_acts, derive_facets, dispatched_call  # type: ignore


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
            "category": None,
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
            "category": None,
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
            "category": tool_policy.get("category") or None,
        }

    tier = _max_tier(payload.get("default_tier"), tool_policy.get("tier"))
    # The control tier gates on its own, independent of the per-act flag. Omitting
    # this is what let Hermes allow control-tier acts outright while PromptForge
    # and the TypeScript reference evaluator gated them.
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
        # Declared on the entry or absent. Never inferred from the act's name: a host that
        # groups approvals must be told the grouping, not left to guess it from a spelling.
        "category": tool_policy.get("category") or None,
    }


_DECISION_RANK = {"allow": 0, "require_approval": 1, "deny": 2}


def evaluate_derived(
    payload: dict,
    tool_name: str,
    args: Optional[dict] = None,
    agent_key: Optional[str] = None,
    pdp_state: str = "normal",
    correlation_id: Optional[str] = None,
    _depth: int = 0,
) -> dict:
    """Evaluate a call against its derived acts, falling back to the base act.

    Layered ABOVE evaluate_against_bundle, never inside it. That function is locked
    line-for-line to the TypeScript reference and proven equivalent by the conformance
    vectors; narrowing is a policy-enforcement-point concern, because only the host knows
    the shape of a tool's arguments. So this composes the reference evaluator instead of
    modifying it, and the decision contract stays byte-identical.

    Semantics:
      * Derive the facets of the call (see derive.py) and evaluate every derived act the
        policy actually lists.
      * Combine most-restrictive-wins — deny over require_approval over allow, and the
        higher tier within a decision. A command that both reaches the network and writes
        a file is judged by whichever facet is governed more tightly.
      * The base act is always among the candidates, so **derivation is monotonic: naming a
        facet can tighten a call's rule and can never loosen it.** This is a security
        property, not a preference. Facets are matched against the command text the calling
        agent composed, so a facet more permissive than its base act would be a permission
        the agent could grant itself by writing the trigger string into a command — append
        `# fleet_msg` and an ungated `terminal.notify` would answer for a `curl`. A facet
        derived from agent-authored text can raise suspicion; it can never certify safety.
        The cost is that carve-outs are impossible — an agent whose `terminal` is gated
        cannot have `terminal.read` ungated — and that cost is correct, because such a
        carve-out would be claimable the same way. Verified against live policy when this
        was tightened: every published base act is ungated and every named facet is gated,
        so no live decision changed.
      * If the policy lists NO derived act, the base act's own decision stands unchanged.
        This is what lets derivation ship to a fleet without denying a call: a policy
        tightens only when it opts in by naming a derived act.
      * "Lists" means **named explicitly in `tools`** — deliberately not resolvable via a
        category. A category like `terminal.write` would otherwise catch every derived
        facet under that domain and silently opt a policy into narrowing nobody wrote,
        making the tightening implicit and surprising in both directions. Derived facets
        are an opt-in tightening; they are claimed by name or not at all. Revisit only
        together with the category mechanism, which is inert today (see the README note on
        category grain).
      * Record why narrowing did or did not apply, always. `derived_act:<name>` when it
        did; `args_unavailable`, `unclassified`, or `derived_unlisted` when it did not.
        Silent non-narrowing would be indistinguishable from having nothing to narrow,
        which is the failure mode this whole codebase is built against.
      * A **dispatcher** (see DISPATCH_ACTS) is unwrapped first: the act it invokes is
        evaluated as itself, and combined most-restrictive-wins with the wrapper's own
        decision. Both are evaluated because that combination can only tighten — if the
        wrapper is refused the call stays refused, and if the wrapper is granted the invoked
        act is now governed where previously nothing was. There is no input under which
        unwrapping permits something the wrapper alone would have permitted.
    """
    if _depth == 0:
        inner_act, inner_args = dispatched_call(tool_name, args)
        if inner_act:
            # The wrapper on its own name, with args withheld so this does not re-unwrap.
            wrapper = evaluate_derived(
                payload, tool_name, None, agent_key, pdp_state, correlation_id, _depth=1
            )
            # The act actually being performed, narrowed by its own arguments in turn — a
            # dispatched `terminal` still derives its shell facets.
            inner = evaluate_derived(
                payload, inner_act, inner_args, agent_key, pdp_state, correlation_id, _depth=1
            )
            chosen_from, chosen = max(
                (("dispatcher", wrapper), ("dispatched", inner)),
                key=lambda pair: (
                    _DECISION_RANK.get(pair[1].get("decision", "deny"), 2),
                    TIER_RANK.get(pair[1].get("tier", "control"), 2),
                ),
            )
            result = dict(chosen)
            reasons = list(result.get("reasons") or [])
            reasons.append(f"dispatched_act:{inner_act}")
            reasons.append(f"decided_on:{chosen_from}")
            result["reasons"] = reasons
            result["dispatched_act"] = inner_act
            return result

    facets, notes = derive_facets(tool_name, args, agent_key)
    candidates = candidate_acts(tool_name, facets)
    tools = payload.get("tools") or {}
    listed = [act for act in candidates if act in tools]

    if not listed:
        result = evaluate_against_bundle(payload, tool_name, pdp_state, correlation_id)
        reasons = list(result.get("reasons") or [])
        reasons.extend(notes)
        if candidates and not notes:
            # Facets were derived but the policy names none of them, so the narrow
            # restriction this call would have hit does not exist yet. Visible, so the
            # gap is reportable rather than merely absent.
            reasons.append("derived_unlisted:" + ",".join(candidates))
        result["reasons"] = reasons
        result["derived_facets"] = facets
        return result

    # The base act is always a candidate, so a facet can only ever tighten its rule. See the
    # monotonicity note in this function's docstring: facets are matched against text the
    # calling agent wrote, so a facet looser than the base act would be one the agent could
    # claim by writing the trigger string into a command.
    evaluated = [
        (act, evaluate_against_bundle(payload, act, pdp_state, correlation_id))
        for act in [tool_name] + listed
    ]
    worst_act, worst = max(
        evaluated,
        key=lambda pair: (
            _DECISION_RANK.get(pair[1].get("decision", "deny"), 2),
            TIER_RANK.get(pair[1].get("tier", "control"), 2),
        ),
    )
    result = dict(worst)
    reasons = list(result.get("reasons") or [])
    reasons.append(f"derived_act:{worst_act}")
    if worst_act == tool_name:
        # The base act was tighter than every facet the policy named. Said out loud because
        # otherwise a facet that never changes an outcome looks like a working control.
        reasons.append("derived_no_tighter_than_base:" + ",".join(listed))
    if len(listed) > 1:
        reasons.append("derived_considered:" + ",".join(listed))
    result["reasons"] = reasons
    result["derived_facets"] = facets
    return result


def category_key_for(tool_name: str) -> Optional[str]:
    """DEPRECATED. The `{domain}.{read|write}` group parsed out of a dotted act name.

    Sole remaining use is the `tool_categories` fallback in resolve_tool_policy — a default
    policy for acts a bundle does not list. It is **not** the approval grain any more.

    It was, and that was a mistake worth recording: deriving the grain from the act's name
    required every name to look like `domain.action`, which is a convention hosts do not
    follow. Hosts send `mcp__server__email_send_now` and `read_file`; neither has a dot, so
    this returned None for every real call and group approval never once fired in
    production. The grain is now declared on the policy entry (`category`), which works for
    any name a host chooses to use and needs no convention at all.

    Kept separate from resolve_tool_policy so the evaluator's return contract stays
    byte-identical to evaluate.ts (the conformance vectors compare both languages against
    the same shape)."""
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
    silently reduce "only a named admin may approve" to "anyone in the channel".
    Eligibility is therefore opt-in per agent, published by PromptForge, and only
    for agents whose channel is already admin-only.
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
    # Exact first, then the canonical dotted form. Order matters for safety, not style: an
    # act that resolves today resolves to the same entry after this change, so
    # canonicalization can only reach entries that were previously unreachable.
    for candidate in resolution_candidates(tool_name):
        if candidate in tools:
            return tools[candidate]

    # DEPRECATED fallback: a default policy for acts the bundle does not list, keyed by a
    # group parsed out of the act name. Only ever matches acts *named* in dotted form, which
    # is not the form hosts send, so in practice it resolves nothing. Retained unchanged for
    # bundles that relied on it; author acts explicitly instead. Do not extend to
    # canonicalized names — that would grant a policy nobody wrote for acts nobody listed.
    #
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

    def evaluate(
        self,
        tool_name: str,
        correlation_id: Optional[str] = None,
        args: Optional[dict] = None,
    ) -> dict:
        """Evaluate a tool call.

        `args` is optional and used only to narrow the act name (see derive.py); the
        decision itself is still made on a name. Omitting it is safe and preserves the
        previous behaviour exactly, but the decision will carry `args_unavailable` so a
        host that stops passing arguments is visible rather than silently coarser.
        """
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

        return evaluate_derived(
            self.bundle.get("payload") or {},
            tool_name,
            args=args,
            agent_key=self.agent_key,
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
