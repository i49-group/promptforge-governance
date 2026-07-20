"""
User-facing PromptForge block / status copy for Hermes.

Messages are returned to the model as tool errors (Hermes pre_tool_call block).
They are written so the agent can relay clear next steps to the human.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

GUIDE_URL = (
    "https://github.com/i49-group/promptforge-governance/blob/main/"
    "docs/guides/ai-governance-configuration-guide.md"
)
INSTALL_URL = (
    "https://github.com/i49-group/promptforge-governance/blob/main/"
    "docs/guides/hermes-plugin-implementation-guide.md"
)
ADMIN_URL = "https://www.mypromptforge.com/admin/ai-governance"


def _bullet(steps: Sequence[str]) -> str:
    return "\n".join(f"  {i}. {s}" for i, s in enumerate(steps, 1))


def classify_setup_error(exc: BaseException | str) -> str:
    text = str(exc).lower()
    if "missing required env var" in text or "pf_" in text and "missing" in text:
        return "missing_env"
    if "401" in text or "unauthorized" in text or "403" in text or "forbidden" in text:
        return "auth"
    if "404" in text or "not found" in text:
        return "not_found"
    if "signature" in text or "verify" in text:
        return "signature"
    if "timed out" in text or "timeout" in text or "connection" in text:
        return "network"
    return "setup"


def block_setup(
    *,
    tool_name: str,
    agent_key: str,
    exc: BaseException | str,
) -> str:
    kind = classify_setup_error(exc)
    detail = str(exc).strip()

    headers = {
        "missing_env": (
            "PromptForge governance is installed, but required environment "
            "variables are missing. Tool calling is blocked until they are set."
        ),
        "auth": (
            "PromptForge rejected this host’s credentials. Tool calling is "
            "blocked until a valid org service token is configured."
        ),
        "not_found": (
            f"No PromptForge governance package is available for agent_key "
            f"`{agent_key}` (pack/bundle not found). Tool calling is blocked "
            "until a package exists with published Act policy."
        ),
        "signature": (
            "PromptForge policy bundle signature verification failed. Tool "
            "calling is blocked until PF_BUNDLE_VERIFY_KEY matches the "
            "PromptForge signing key."
        ),
        "network": (
            "Could not reach PromptForge to load governance policy. Tool "
            "calling is blocked (fail-closed) until connectivity is restored."
        ),
        "setup": (
            "PromptForge governance could not load a policy bundle. Tool "
            "calling is blocked until governance is configured."
        ),
    }

    steps_by_kind = {
        "missing_env": [
            "Set PF_BASE_URL, PF_SERVICE_TOKEN, PF_BUNDLE_VERIFY_KEY, and PF_AGENT_KEY on this Hermes host/profile.",
            f"Use PF_AGENT_KEY=`{agent_key}` matching the PromptForge package.",
            f"Follow: {INSTALL_URL}",
        ],
        "auth": [
            "In PromptForge: Admin → Integrations → create an org service credential with `governance` scope.",
            "Put the token in PF_SERVICE_TOKEN and restart the Hermes gateway/profile.",
            "Confirm the token belongs to the same org that owns the AI Governance package.",
        ],
        "not_found": [
            f"Open {ADMIN_URL}",
            f"Create or open the package for agent_key `{agent_key}` (New package).",
            "Ensure Talk layers exist and Act policy is Published (not draft-only).",
            f"Config guide: {GUIDE_URL}",
        ],
        "signature": [
            "Copy GOVERNANCE_BUNDLE_SIGNING_KEY from the PromptForge deploy into PF_BUNDLE_VERIFY_KEY on Hermes.",
            "Restart Hermes after updating the key.",
            f"See: {INSTALL_URL}",
        ],
        "network": [
            f"Check that this host can reach PF_BASE_URL (default https://www.mypromptforge.com).",
            "Retry after network/VPN/Tailscale is up; tools stay blocked until refresh succeeds.",
        ],
        "setup": [
            f"Open {ADMIN_URL} and confirm a package for `{agent_key}` with published Act.",
            "Verify PF_BASE_URL / PF_SERVICE_TOKEN / PF_BUNDLE_VERIFY_KEY / PF_AGENT_KEY.",
            f"Guides: {GUIDE_URL} · {INSTALL_URL}",
        ],
    }

    return (
        f"🚫 TOOL BLOCKED BY PROMPTFORGE\n"
        f"Tool: `{tool_name}`\n"
        f"Agent: `{agent_key}`\n\n"
        f"{headers[kind]}\n\n"
        f"What to do:\n{_bullet(steps_by_kind[kind])}\n\n"
        f"Technical detail: {detail}"
    )


def block_policy(
    *,
    tool_name: str,
    agent_key: str,
    decision: str,
    reasons: Sequence[str],
    bundle_version: str,
    pdp_state: str,
) -> str:
    reason_l = [r.lower() for r in reasons]
    joined = ", ".join(reasons) if reasons else "policy"

    if decision == "require_approval" or any(
        "requires_approval" in r for r in reason_l
    ):
        title = "This tool requires human approval under PromptForge Act policy."
        steps = [
            "Have an operator approve the action in your ops host (e.g. Brilliant Central approval card), or",
            f"In PromptForge ({ADMIN_URL}) open agent `{agent_key}` → Act policy → set requires_approval=false for `{tool_name}` if that is intentional → Publish.",
            "Then retry the request (hosts refresh bundles on a schedule; wait up to a few minutes or restart Hermes).",
        ]
    elif any("unknown_tool" in r for r in reason_l):
        title = (
            f"`{tool_name}` is not in the published Act inventory for `{agent_key}`."
        )
        steps = [
            f"Open {ADMIN_URL} → package `{agent_key}` → Act policy.",
            f"Add tool `{tool_name}` with the correct tier / granted / requires_approval.",
            "Click Publish.",
            "Retry after the host refreshes the signed bundle.",
        ]
    elif any("not_granted" in r for r in reason_l):
        title = f"`{tool_name}` is explicitly not granted in PromptForge Act policy."
        steps = [
            f"Open {ADMIN_URL} → package `{agent_key}` → Act policy.",
            f"Set granted=true for `{tool_name}` (if appropriate) → Publish.",
            "Retry after bundle refresh.",
        ]
    elif any("fail_closed" in r for r in reason_l) or pdp_state == "fail_closed":
        title = (
            "PromptForge policy cache is expired past grace (fail-closed). "
            "Tool calling stays blocked until a fresh signed bundle is loaded."
        )
        steps = [
            "Confirm PromptForge is reachable and Act is still published.",
            "Check PF_SERVICE_TOKEN is valid; restart Hermes to force refresh.",
            f"Install/refresh help: {INSTALL_URL}",
        ]
    else:
        title = f"PromptForge Act policy denied `{tool_name}`."
        steps = [
            f"Review Act policy for `{agent_key}` at {ADMIN_URL}.",
            "Adjust granted / tier / requires_approval → Publish.",
            f"Config guide: {GUIDE_URL}",
        ]

    return (
        f"🚫 TOOL BLOCKED BY PROMPTFORGE\n"
        f"Tool: `{tool_name}`\n"
        f"Agent: `{agent_key}`\n"
        f"Decision: {decision} · Bundle: {bundle_version} · PDP state: {pdp_state}\n\n"
        f"{title}\n\n"
        f"What to do:\n{_bullet(steps)}\n\n"
        f"Reasons: {joined}\n"
        f"Tell the user clearly that PromptForge blocked this tool and summarize the steps above."
    )


def block_missing_tool_name(agent_key: str) -> str:
    return (
        "🚫 TOOL BLOCKED BY PROMPTFORGE\n"
        f"Agent: `{agent_key}`\n\n"
        "Hermes invoked a tool without a tool name. Nothing was executed.\n\n"
        "What to do:\n"
        "  1. Retry the request.\n"
        "  2. If it persists, report a Hermes tool-dispatch bug to your platform operator."
    )


def session_not_ready_context(
    *,
    agent_key: str,
    error: Optional[str] = None,
) -> str:
    """Injected via pre_llm_call so the model warns the user before tools fail."""
    detail = f"\nLast error: {error}" if error else ""
    return (
        "[PromptForge governance status: NOT READY — tool calling is blocked]\n"
        f"agent_key=`{agent_key}`.{detail}\n"
        "Tell the user up front that tools will not run until PromptForge is configured:\n"
        f"  • Admin: {ADMIN_URL}\n"
        f"  • Create/publish package for `{agent_key}`\n"
        f"  • Check PF_* env on this Hermes host\n"
        f"  • Guide: {GUIDE_URL}\n"
        "Do not pretend a blocked tool succeeded."
    )


def session_ready_context(
    *,
    agent_key: str,
    bundle_version: str,
    pack_version: str,
) -> Optional[str]:
    """Optional short status; keep quiet unless PF_ANNOUNCE_READY=true."""
    import os

    if os.environ.get("PF_ANNOUNCE_READY", "").lower() not in {
        "1",
        "true",
        "yes",
    }:
        return None
    return (
        f"[PromptForge governance active for `{agent_key}` — "
        f"Act bundle {bundle_version}, Talk pack {pack_version}. "
        "Tool calls are enforced by PromptForge policy.]"
    )


def extract_http_status(exc: BaseException | str) -> Optional[int]:
    m = re.search(r"HTTP\s+(\d{3})", str(exc))
    return int(m.group(1)) if m else None
