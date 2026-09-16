"""
Act-name canonicalization.

Policies are *written* in a dotted `domain.action` form. Hosts *emit* MCP acts as
`mcp__<server>__<domain>_<action>`. Nothing reconciled the two, so every dotted entry in
every policy was unreachable and category-level rules — which key on the dot — never
resolved at all.

The canonical form is the **dotted** one, and that is structural rather than a preference:
the category mechanism is defined on `domain.action`, so if the prefixed spelling were
canonical there would be no dot to derive a category from. The emitted name is therefore
normalised onto the stored one, never the reverse.

Two rules govern how this is used, both load-bearing:

  * **Exact match always wins.** A policy that names the emitted spelling keeps resolving
    to that entry, so canonicalization cannot change an answer that already resolves. It
    can only reach entries that were previously unreachable.
  * **Server identity is discarded deliberately.** `mcp__example_server__email_send_now`
    and the same act behind a differently-named server canonicalize alike. That is the
    point — a policy should not break because a host renamed an MCP server — and it is
    also a real limitation: if two servers expose the same `domain_action` pair, one policy
    entry governs both. Use the exact prefixed spelling when they must differ.

Kept in its own module because the reference implementation in
`packages/governance-pdp/src/actname.ts` must stay line-for-line equivalent, and the
conformance vectors compare the two.
"""

from __future__ import annotations

import re
from typing import List, Optional

# `mcp__<server>__<rest>`. The server segment may itself contain single underscores
# (`brilliant_central`), so the separator matched is the DOUBLE underscore — anchored, and
# non-greedy up to the first `__` that is followed by the remainder.
_MCP_PREFIX = re.compile(r"^mcp__(?P<server>[a-z0-9]+(?:_[a-z0-9]+)*)__(?P<rest>.+)$", re.I)

# A single-underscore variant appears in older host output
# (`mcp_example_server_calendar_create_event`). It is genuinely ambiguous — nothing
# distinguishes the server segment from the domain segment — so it is NOT canonicalized.
# Guessing here would silently map an act onto the wrong policy entry, which is worse than
# denying it: see the note in `is_ambiguous_mcp_name`.
_MCP_SINGLE = re.compile(r"^mcp_(?!_)[a-z0-9_]+$", re.I)


def is_ambiguous_mcp_name(act: str) -> bool:
    """
    True for single-underscore MCP spellings, which cannot be canonicalized safely.

    `mcp_example_server_calendar_create_event` could split as server `example` / domain
    `server`, or server `example_server` / domain `calendar`, and there is no way to tell
    from the string. Such a name should keep denying as an unknown act — loudly — rather
    than be mapped by a guess onto a policy entry that may grant something else entirely.
    """
    return bool(_MCP_SINGLE.match(act)) and not _MCP_PREFIX.match(act)


def canonical_act(act: str) -> Optional[str]:
    """
    The dotted canonical form of an emitted act name, or None when there is nothing to
    canonicalize (a native act, an already-dotted act, or an ambiguous spelling).

    `mcp__example_server__email_send_now` → `email.send_now`
    `mcp__example_server__contacts_get_segment_members_hashed`
        → `contacts.get_segment_members_hashed`
    `terminal` → None (native acts have no domain/action shape and need none)
    `email.send_now` → None (already canonical)
    """
    if not act or "." in act:
        return None

    match = _MCP_PREFIX.match(act)
    if not match:
        return None

    rest = match.group("rest")
    # First underscore-separated token is the domain; everything after it is the action.
    # `email_send_now` → `email` + `send_now`. A rest with no underscore (`email`) has no
    # action, so there is no dotted form to build.
    domain, sep, action = rest.partition("_")
    if not sep or not domain or not action:
        return None
    return f"{domain}.{action}"


def resolution_candidates(act: str) -> List[str]:
    """
    Policy keys to try, in precedence order. The emitted name first — so an exact entry
    always wins and no resolving act changes answer — then the canonical dotted form.
    """
    candidates = [act]
    canon = canonical_act(act)
    if canon and canon != act:
        candidates.append(canon)
    return candidates
