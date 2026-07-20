"""
PromptForge governance PEP for Hermes Agent.

Registers pre_tool_call to block denied tools using a locally cached,
signature-verified PromptForge policy bundle.

Block contract (Hermes):
  return {"action": "block", "message": "..."}
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

try:
    from .pdp import GovernancePdp, PdpError
    from . import messages as msg
except ImportError:  # loaded as flat plugin directory on sys.path
    from pdp import GovernancePdp, PdpError  # type: ignore
    import messages as msg  # type: ignore

logger = logging.getLogger("promptforge.governance")

_pdp: Optional[GovernancePdp] = None
_lock = threading.Lock()
_refresh_stop = threading.Event()
_refresh_thread: Optional[threading.Thread] = None

# Session UX state — drives pre_llm_call warning when Act is not loaded
_governance_ready = False
_last_setup_error: Optional[str] = None
_warned_user = False

DEFAULT_REFRESH_SECONDS = 600


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
            return None

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
    ctx.register_hook("pre_llm_call", pre_llm_call)
    logger.info(
        "Registered promptforge-governance hooks (agent_key=%s)",
        os.environ.get("PF_AGENT_KEY", "?"),
    )


_ = time
