"""
PromptForge governance PEP for Hermes Agent.

Registers pre_tool_call to block denied tools using a locally cached,
signature-verified PromptForge policy bundle.

Directive contract (Hermes):
  return {"action": "block", "message": "..."}    — refuse outright
  return {"action": "approve", "message", "rule_key"} — escalate to Hermes's own
      human-approval gate, which asks in whatever channel the agent is in

We use "approve" only for PromptForge's `require_approval` decision, and only for
agents PromptForge has marked eligible. `deny` always blocks: letting a human wave
through an act policy refuses outright is privilege escalation, which is a separate
mechanism with its own binding and expiry.

post_tool_call reports back to PromptForge, opt-in per agent via `report_decisions`.
Decisions are reported at the point they are made; an escalated act is additionally
reported once it has actually run, which is the only evidence available here that a
human approved it.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Optional

try:
    from .pdp import GovernancePdp, PdpError
    from . import pdp as pdp_mod
    from . import messages as msg
    from .reporter import DecisionReporter
except ImportError:  # loaded as flat plugin directory on sys.path
    from pdp import GovernancePdp, PdpError  # type: ignore
    import pdp as pdp_mod  # type: ignore
    import messages as msg  # type: ignore
    from reporter import DecisionReporter  # type: ignore

logger = logging.getLogger("promptforge.governance")

_pdp: Optional[GovernancePdp] = None
_lock = threading.Lock()
_refresh_stop = threading.Event()
_refresh_thread: Optional[threading.Thread] = None

# Session UX state — drives pre_llm_call warning when Act is not loaded
_governance_ready = False
_last_setup_error: Optional[str] = None
_warned_user = False

# Halved now that refreshes are conditional (304s are nearly free), which bounds how
# long a policy *tightening* can go unnoticed. Loosening no longer waits for this
# timer at all — a denial revalidates on the spot.
DEFAULT_REFRESH_SECONDS = 300


# Escalated acts awaiting a human answer, keyed by tool call. Bounded: an approval
# nobody ever answers must not accumulate. Oldest is evicted, which loses only the
# post-run confirmation for a very stale call — the require_approval row is already
# recorded by then.
_PENDING_MAX = 64
_pending_lock = threading.Lock()
_pending_escalations: "OrderedDict[str, dict]" = OrderedDict()

_reporter: Optional[DecisionReporter] = None
_reporter_lock = threading.Lock()


def _get_reporter(pdp: GovernancePdp) -> DecisionReporter:
    global _reporter
    with _reporter_lock:
        if _reporter is None:
            _reporter = DecisionReporter(
                base_url=pdp.base_url,
                token=pdp.token,
                environment=pdp.environment,
            )
        return _reporter


def _reporting_enabled(pdp: GovernancePdp) -> bool:
    bundle = pdp.bundle or {}
    return pdp_mod.report_decisions_enabled(bundle.get("payload") or {})


def _report(
    pdp: GovernancePdp,
    *,
    tool_name: str,
    decision: str,
    reasons: Optional[list] = None,
    correlation_id: Optional[str] = None,
) -> None:
    """Best-effort. Silent when the policy has not opted in, and never raises —
    a tool call must not fail because we could not describe it."""
    if not _reporting_enabled(pdp):
        return
    try:
        _get_reporter(pdp).report(
            agent_key=pdp.agent_key,
            tool_name=tool_name,
            decision=decision,
            reasons=reasons or [],
            correlation_id=correlation_id,
            policy_version=pdp.bundle_version,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("PromptForge decision reporting skipped: %s", exc)


def _escalation_key(tool_name: str, task_id: str, tool_call_id: str) -> str:
    """Hermes passes tool_call_id to both hooks; the composite is a fallback for
    hosts or tests that do not."""
    return tool_call_id or f"{task_id}:{tool_name}"


def _remember_escalation(key: str, info: dict) -> None:
    with _pending_lock:
        _pending_escalations[key] = info
        _pending_escalations.move_to_end(key)
        while len(_pending_escalations) > _PENDING_MAX:
            _pending_escalations.popitem(last=False)


def _take_escalation(key: str) -> Optional[dict]:
    with _pending_lock:
        return _pending_escalations.pop(key, None)


def _inline_approval_allowed(pdp: GovernancePdp) -> bool:
    """Whether this agent may have gated acts escalated to Hermes's approval gate.

    Off unless PromptForge published it. Hermes keeps no approver identity and has no
    role concept, so its gate asks whoever is present; enabling this for an agent
    whose channel is not already restricted to named admins would quietly turn
    "only an administrator may approve" into "anyone in the room".
    """
    bundle = pdp.bundle or {}
    return pdp_mod.inline_approval_enabled(bundle.get("payload") or {})


def _agent_key_label() -> str:
    return (os.environ.get("PF_AGENT_KEY") or "unknown").strip().lower() or "unknown"


def _env(name: str, default: Optional[str] = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not str(value).strip():
        raise PdpError(f"Missing required env var: {name}")
    return str(value).strip()


def _get_pdp() -> GovernancePdp:
    global _pdp
    with _lock:
        if _pdp is None:
            _pdp = GovernancePdp(
                base_url=_env("PF_BASE_URL"),
                token=_env("PF_SERVICE_TOKEN"),
                verify_key=_env("PF_BUNDLE_VERIFY_KEY"),
                agent_key=_env("PF_AGENT_KEY").lower(),
                environment=os.environ.get("PF_ENVIRONMENT", "production").strip()
                or "production",
                timeout_s=float(os.environ.get("PF_FETCH_TIMEOUT_S", "2")),
            )
        return _pdp


def _mark_ready(meta: dict) -> None:
    global _governance_ready, _last_setup_error, _warned_user
    _governance_ready = True
    _last_setup_error = None
    _warned_user = False
    logger.info(
        "PromptForge governance ready agent=%s pack=%s bundle=%s state=%s",
        _get_pdp().agent_key,
        meta.get("pack_version"),
        meta.get("bundle_version"),
        meta.get("state"),
    )


def _mark_not_ready(exc: BaseException | str) -> None:
    global _governance_ready, _last_setup_error, _warned_user
    _governance_ready = False
    _last_setup_error = str(exc)
    _warned_user = False
    logger.error("PromptForge governance not ready: %s", exc)


def _refresh_loop(interval_s: float) -> None:
    while not _refresh_stop.wait(interval_s):
        try:
            meta = _get_pdp().refresh()
            _mark_ready(meta)
        except Exception as exc:  # noqa: BLE001 — never crash Hermes
            _mark_not_ready(exc)
            logger.warning("PromptForge refresh failed: %s", exc)


def _start_refresh_loop() -> None:
    global _refresh_thread
    interval = float(os.environ.get("PF_REFRESH_SECONDS", str(DEFAULT_REFRESH_SECONDS)))
    if interval <= 0:
        return
    if _refresh_thread and _refresh_thread.is_alive():
        return
    _refresh_stop.clear()
    _refresh_thread = threading.Thread(
        target=_refresh_loop,
        args=(interval,),
        name="promptforge-governance-refresh",
        daemon=True,
    )
    _refresh_thread.start()


def on_session_start(**kwargs: Any) -> None:
    """Load pack+bundle at session start; fail-closed on later tool calls if this fails."""
    global _warned_user
    _warned_user = False
    try:
        meta = _get_pdp().refresh()
        _mark_ready(meta)
        _start_refresh_loop()
    except Exception as exc:  # noqa: BLE001
        _mark_not_ready(exc)


def pre_tool_call(
    tool_name: str = "",
    args: Optional[dict] = None,
    task_id: str = "",
    **kwargs: Any,
) -> Optional[dict]:
    """
    PEP gate. Returns Hermes block directive with clear user-facing next steps.
    """
    agent = _agent_key_label()
    name = (tool_name or kwargs.get("name") or "").strip()
    if not name:
        return {"action": "block", "message": msg.block_missing_tool_name(agent)}

    try:
        pdp = _get_pdp()
        if pdp.bundle is None:
            try:
                meta = pdp.refresh()
                _mark_ready(meta)
            except Exception as exc:  # noqa: BLE001
                _mark_not_ready(exc)
                return {
                    "action": "block",
                    "message": msg.block_setup(
                        tool_name=name, agent_key=agent, exc=exc
                    ),
                }

        result = pdp.evaluate(name, correlation_id=task_id or None)
        if result["decision"] == "allow":
            _report(
                pdp,
                tool_name=name,
                decision="allow",
                reasons=result.get("reasons") or [],
                correlation_id=result.get("correlation_id"),
            )
            return None

        # A denial is the strongest available signal that our copy of the policy may
        # be stale — it is exactly when an operator has just published a fix. Spend
        # one conditional revalidation before refusing; a 304 costs almost nothing and
        # this is what turns "fix and wait" into "fix and retry".
        try:
            meta = pdp.refresh()
            _mark_ready(meta)
            result = pdp.evaluate(name, correlation_id=task_id or None)
            if result["decision"] == "allow":
                _report(
                    pdp,
                    tool_name=name,
                    decision="allow",
                    reasons=(result.get("reasons") or []) + ["revalidated"],
                    correlation_id=result.get("correlation_id"),
                )
                return None
        except Exception as exc:  # noqa: BLE001 — keep the original decision
            logger.debug("PromptForge revalidation before block failed: %s", exc)

        if result["decision"] == "require_approval" and _inline_approval_allowed(pdp):
            scope = pdp_mod.category_key_for(name)
            # Recorded as require_approval, not as an allow. Hermes decides what the
            # human says; all we know here is that policy sent it to one.
            _report(
                pdp,
                tool_name=name,
                decision="require_approval",
                reasons=(result.get("reasons") or []) + ["escalated_to_host_gate"],
                correlation_id=result.get("correlation_id"),
            )
            _remember_escalation(
                _escalation_key(name, task_id, str(kwargs.get("tool_call_id") or "")),
                {
                    "tool_name": name,
                    "reasons": result.get("reasons") or [],
                    "correlation_id": result.get("correlation_id"),
                },
            )
            return {
                "action": "approve",
                "message": msg.approval_prompt(
                    tool_name=name,
                    agent_key=pdp.agent_key or agent,
                    tier=str(result.get("tier") or "unknown"),
                    scope=scope,
                ),
                # Grain for Hermes's own allowlist. Category-level so one answer
                # clears the rest of the chain in that domain instead of one link.
                "rule_key": scope or name,
            }

        # Only deny and require_approval reach here; allow returned above. Both are
        # valid at the endpoint now, so the decision passes through unflattened.
        _report(
            pdp,
            tool_name=name,
            decision=result["decision"],
            reasons=(result.get("reasons") or []) + ["blocked_by_host"],
            correlation_id=result.get("correlation_id"),
        )
        return {
            "action": "block",
            "message": msg.block_policy(
                tool_name=name,
                agent_key=pdp.agent_key or agent,
                decision=result["decision"],
                reasons=result.get("reasons") or [],
                bundle_version=str(result.get("bundle_version") or "unknown"),
                pdp_state=str(result.get("pdp_state") or "unknown"),
            ),
        }
    except PdpError as exc:
        _mark_not_ready(exc)
        return {
            "action": "block",
            "message": msg.block_setup(
                tool_name=name, agent_key=agent, exc=exc
            ),
        }
    except Exception as exc:  # noqa: BLE001 — fail closed
        logger.exception("PromptForge evaluate error")
        _mark_not_ready(exc)
        return {
            "action": "block",
            "message": msg.block_setup(
                tool_name=name, agent_key=agent, exc=exc
            ),
        }


def post_tool_call(
    tool_name: str = "",
    task_id: str = "",
    tool_call_id: str = "",
    **kwargs: Any,
) -> None:
    """Record that a human-gated act actually ran.

    Hermes owns the approval prompt and does not hand us its verdict, but it only
    reaches the tool if the answer was yes — so an escalated act arriving here is an
    approval that was granted and used. This is also why reporting an approval
    happens after the act rather than before it.

    An escalation that never arrives here was declined or timed out, and shows up as
    a require_approval row with no following run. We cannot yet tell those two apart;
    doing so needs a verdict from Hermes's gate that it does not currently expose.
    """
    name = (tool_name or kwargs.get("name") or "").strip()
    if not name:
        return None

    pending = _take_escalation(_escalation_key(name, task_id, tool_call_id))
    if pending is None:
        return None

    try:
        pdp = _get_pdp()
    except Exception:  # noqa: BLE001
        return None

    _report(
        pdp,
        tool_name=name,
        decision="allow",
        reasons=(pending.get("reasons") or []) + ["human_approved", "ran"],
        correlation_id=pending.get("correlation_id"),
    )
    return None


def pre_llm_call(**kwargs: Any) -> Optional[dict]:
    """
    Talk injection + proactive UX when governance is not ready.

    When Act cannot load, inject a clear status so the model warns the user
    before the first tool attempt fails.
    """
    global _warned_user
    parts: list[str] = []

    if not _governance_ready:
        # Warn once per session (or until ready), unless PF_WARN_EVERY_TURN=true
        every = os.environ.get("PF_WARN_EVERY_TURN", "").lower() in {
            "1",
            "true",
            "yes",
        }
        if every or not _warned_user:
            parts.append(
                msg.session_not_ready_context(
                    agent_key=_agent_key_label(),
                    error=_last_setup_error,
                )
            )
            _warned_user = True
    else:
        try:
            pdp = _get_pdp()
            ready = msg.session_ready_context(
                agent_key=pdp.agent_key,
                bundle_version=pdp.bundle_version,
                pack_version=str((pdp.pack or {}).get("version") or "unknown"),
            )
            if ready:
                parts.append(ready)
        except Exception:  # noqa: BLE001
            pass

    inject_talk = os.environ.get("PF_INJECT_TALK", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    if inject_talk and _governance_ready:
        try:
            talk = _get_pdp().talk_system_prompt()
            if talk:
                parts.append(f"[PromptForge Talk pack]\n{talk}")
        except Exception:  # noqa: BLE001
            pass

    if not parts:
        return None
    return {"context": "\n\n".join(parts)}


def register(ctx: Any) -> None:
    """Hermes plugin entrypoint."""
    ctx.register_hook("on_session_start", on_session_start)
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("post_tool_call", post_tool_call)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    logger.info(
        "Registered promptforge-governance hooks (agent_key=%s)",
        os.environ.get("PF_AGENT_KEY", "?"),
    )
