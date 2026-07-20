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
except ImportError:  # loaded as flat plugin directory on sys.path
    from pdp import GovernancePdp, PdpError  # type: ignore

logger = logging.getLogger("promptforge.governance")

_pdp: Optional[GovernancePdp] = None
_lock = threading.Lock()
_refresh_stop = threading.Event()
_refresh_thread: Optional[threading.Thread] = None

DEFAULT_REFRESH_SECONDS = 600


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


def _refresh_loop(interval_s: float) -> None:
    while not _refresh_stop.wait(interval_s):
        try:
            _get_pdp().refresh()
            logger.info(
                "PromptForge bundle refreshed (version=%s state=%s)",
                _get_pdp().bundle_version,
                _get_pdp().state,
            )
        except Exception as exc:  # noqa: BLE001 — never crash Hermes
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
    try:
        meta = _get_pdp().refresh()
        logger.info(
            "PromptForge governance ready agent=%s pack=%s bundle=%s state=%s",
            _get_pdp().agent_key,
            meta.get("pack_version"),
            meta.get("bundle_version"),
            meta.get("state"),
        )
        _start_refresh_loop()
    except Exception as exc:  # noqa: BLE001
        logger.error("PromptForge session refresh failed: %s", exc)


def pre_tool_call(
    tool_name: str = "",
    args: Optional[dict] = None,
    task_id: str = "",
    **kwargs: Any,
) -> Optional[dict]:
    """
    PEP gate. Returns Hermes block directive when Act denies / requires approval
    (approval without host UX is treated as block — fail-closed).
    """
    name = (tool_name or kwargs.get("name") or "").strip()
    if not name:
        return {
            "action": "block",
            "message": "PromptForge denied: missing tool_name",
        }

    try:
        pdp = _get_pdp()
        if pdp.bundle is None:
            # Attempt one refresh before fail-closed
            try:
                pdp.refresh()
            except Exception as exc:  # noqa: BLE001
                return {
                    "action": "block",
                    "message": (
                        f"PromptForge deny (no policy bundle): {exc}. "
                        "Publish Act in PromptForge and check PF_* env vars."
                    ),
                }

        result = pdp.evaluate(name, correlation_id=task_id or None)
        if result["decision"] == "allow":
            return None

        reasons = ", ".join(result.get("reasons") or [])
        version = result.get("bundle_version") or "unknown"
        state = result.get("pdp_state") or "unknown"

        if result["decision"] == "require_approval":
            # Hermes has no built-in PF approval card here — fail closed.
            return {
                "action": "block",
                "message": (
                    f"PromptForge requires approval for `{name}` "
                    f"(bundle {version}, state={state}): {reasons}. "
                    "Approve in your ops host (e.g. Brilliant Central) or "
                    "set requires_approval=false in the published Act policy."
                ),
            }

        return {
            "action": "block",
            "message": (
                f"PromptForge denied `{name}` "
                f"(bundle {version}, state={state}): {reasons}"
            ),
        }
    except Exception as exc:  # noqa: BLE001 — fail closed
        logger.exception("PromptForge evaluate error")
        return {
            "action": "block",
            "message": f"PromptForge deny (evaluate error): {exc}",
        }


def pre_llm_call(**kwargs: Any) -> Optional[dict]:
    """Optional Talk injection from content pack (prefer-PF). Not a security boundary."""
    if os.environ.get("PF_INJECT_TALK", "true").lower() in {"0", "false", "no"}:
        return None
    try:
        talk = _get_pdp().talk_system_prompt()
        if not talk:
            return None
        return {"context": f"[PromptForge Talk pack]\n{talk}"}
    except Exception:  # noqa: BLE001
        return None


def register(ctx: Any) -> None:
    """Hermes plugin entrypoint."""
    ctx.register_hook("on_session_start", on_session_start)
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    logger.info(
        "Registered promptforge-governance hooks (agent_key=%s)",
        os.environ.get("PF_AGENT_KEY", "?"),
    )


# Avoid unused import warning for time in some linters
_ = time
