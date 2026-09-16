"""
Derive a specific act name from what a general-purpose call actually does.

WHY THIS EXISTS

The PDP decides on an act name. That works while act names describe capabilities — and
breaks completely for acts that describe a *mechanism*. `terminal` is not a capability;
it is every capability, wearing one name. Measured over seven days on two agents,
`terminal` was 1,787 calls: fetching URLs, signalling processes, writing files,
scheduling future work, and messaging peers, all resolving to a single decision.

The consequence was observed rather than theorised. A policy that denied a browser act
while granting `terminal` was satisfied with `curl` seconds later. One that denied the
inter-agent bus was satisfied by invoking the fleet script over the shell. The denial was
real, logged, and correct — and it constrained a label.

Putting `terminal` behind approval was measured and rejected: ~250 prompts a day across
two agents, which is a control that gets switched off in its first week.

So instead of asking the PDP to look at arguments, this narrows the *name* before the
PDP sees it. `terminal` running `curl` is offered as `terminal.network`. The PDP stays
purely name-based — no change to the decision contract, the reference evaluator, or the
conformance shape — while the name it decides on finally means something.

THREE RULES THAT MAKE THIS SAFE

1. **Most restrictive wins.** A command can have several facets at once
   (`curl … > out.json` is network AND write). Every facet present in the policy is
   evaluated and the tightest answer applies. Never the first match, never the loosest.

2. **Unknown narrows nothing, and says so.** If the host passes no arguments, or the
   command cannot be classified, derivation returns the base act and marks the decision
   `args_unavailable` / `unclassified`. Existing policies therefore keep working
   unchanged — but the decision trail records that the finer grain did not apply, because
   a control that quietly stops narrowing is the precise failure this codebase exists to
   make visible.

3. **Patterns are evidence, not intuition.** Every pattern below carries the mechanism it
   detects. Add one when you can name a command that exercises it. Removing a pattern to
   make a policy pass is backwards: the policy is what changes.

MIGRATION SHAPE

Derived acts are opt-in per policy. Until a policy lists `terminal.network`, a `curl`
resolves to `terminal` exactly as today. Adding the derived act is what activates the
constraint — so this can ship to a fleet without denying a single call, and each policy
tightens on its own schedule.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

# Facets, tightest-consequence first. Order here is documentation only; the decision
# combines every applicable facet rather than picking one.
FACET_CREDENTIAL = "credential"
FACET_CROSS_PROFILE = "cross_profile"
FACET_DELEGATE = "delegate"
FACET_NOTIFY = "notify"
FACET_SCHEDULE = "schedule"
FACET_PROCESS = "process"
FACET_NETWORK = "network"
FACET_WRITE = "write"
FACET_READ = "read"

# Acts that name a mechanism rather than a capability, and are therefore worth narrowing.
DERIVABLE_ACTS = ("terminal", "execute_code", "read_file", "write_file", "patch")

# Acts that perform no capability of their own and invoke another act by name. These are a
# harder case than `terminal`: a shell command at least has a facet worth deriving, whereas a
# dispatcher's own name carries no information at all. Governing the wrapper as a name has only
# two settings, and both are wrong — grant it and every act it can reach is ungoverned, refuse
# it and the agent loses its entire dispatch path. Measured on two agents: 566 and 799 calls,
# 32 and 30 distinct acts underneath, including live customer campaign sends that an approval
# gate on the act's own name could never see.
DISPATCH_ACTS = ("tool_call",)

# The act name used when a dispatcher's target cannot be read. Matches no policy entry by
# design, so it denies rather than passing as an unremarkable call.
UNRESOLVED_DISPATCH = "<unresolved dispatch>"

# Argument keys hosts use for the command or code body. Checked in order.
_COMMAND_KEYS = ("command", "cmd", "script", "code", "input", "shell", "commands")
_PATH_KEYS = ("path", "file", "file_path", "filename", "target", "paths")

# Where a dispatcher carries the act it invokes, and that act's own arguments.
_DISPATCH_NAME_KEYS = ("name", "tool", "tool_name")
_DISPATCH_ARG_KEYS = ("arguments", "args", "input", "parameters")


def _first_str(args: Dict, keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
        # Some hosts pass a list of commands or paths.
        if isinstance(value, (list, tuple)) and value:
            joined = " ".join(str(v) for v in value if isinstance(v, (str, int, float)))
            if joined.strip():
                return joined
    return None


# Each pattern is (facet, compiled regex, mechanism it detects).
#
# Word boundaries matter: `\bkill\b` must not fire on "killer" and `\bcp\b` must not fire
# on "cpu". Patterns are matched case-insensitively against the whole command string.
_COMMAND_PATTERNS: List[Tuple[str, "re.Pattern[str]", str]] = [
    # Network. Includes bare URLs because a heredoc'd Python fetch has no curl in it.
    (FACET_NETWORK, re.compile(r"\b(curl|wget|nc|ncat|telnet|ssh|scp|sftp|rsync)\b", re.I),
     "network client invoked directly"),
    (FACET_NETWORK, re.compile(r"https?://", re.I), "URL present in the command"),
    (FACET_NETWORK, re.compile(r"\b(urllib|requests|httpx|aiohttp|socket)\b", re.I),
     "network library referenced in inline code"),
    # Process control.
    (FACET_PROCESS, re.compile(r"\b(kill|pkill|killall|ps|pgrep|pidof|top)\b", re.I),
     "process inspection or signalling"),
    (FACET_PROCESS, re.compile(r"\b(launchctl|systemctl|service|nohup|disown)\b", re.I),
     "service or daemon control"),
    (FACET_PROCESS, re.compile(r"\b(subprocess|os\.system|os\.exec|popen)\b", re.I),
     "process spawn from inline code"),
    # Scheduling — unattended future execution, which no synchronous review can catch.
    (FACET_SCHEDULE, re.compile(r"\b(crontab|cron)\b", re.I), "cron manipulation"),
    (FACET_SCHEDULE, re.compile(r"\bat\s+(now|\d{1,2}:\d{2})", re.I), "at(1) scheduling"),
    (FACET_SCHEDULE, re.compile(r"launchctl\s+(load|bootstrap|enable)", re.I),
     "launchd job activation"),
    # Peer contact, split in two because one pattern for both charged an approval for logging.
    #
    # Messaging a peer puts a message in its inbox; the peer decides whether to act, under its
    # own policy, as it would for a human. That is notification and it is the high-volume shape:
    # of 354 peer-contact commands measured across two agents, 107 were an agent posting
    # "LOGGED: …" to a decision log. Gating those buys nothing and costs an approval each.
    #
    # Handing over work is the shape that matters, and it is rare — single digits over months.
    # It is also the one capability that can move an act outside the caller's policy, so it is
    # kept separate to be governed separately. Note this pattern catches the shell spelling; the
    # native `delegate_task` tool is governed by its own name.
    #
    # Deliberately NOT keyed on the message body or a --task label: those are free text an agent
    # writes about its own work, which makes them trivially avoidable and no basis for a control.
    (FACET_NOTIFY, re.compile(r"fleet_msg|fleet/|message_agent", re.I),
     "message sent to a peer agent"),
    (FACET_DELEGATE, re.compile(r"delegate_task", re.I),
     "work handed to a peer agent, which executes it under its own policy"),
    # Credential access.
    (FACET_CREDENTIAL,
     re.compile(r"\.env\b|credentials|id_rsa|id_ed25519|\.pem\b|_TOKEN|_SECRET|_KEY\b", re.I),
     "secret material referenced by name"),
    # Filesystem mutation.
    (FACET_WRITE, re.compile(r">>?[^>]|(\btee\b)|(\bdd\b)|(\btruncate\b)"),
     "output redirection or block write"),
    (FACET_WRITE, re.compile(r"\b(rm|mv|cp|mkdir|rmdir|touch|chmod|chown|ln)\b", re.I),
     "filesystem mutation command"),
    (FACET_WRITE, re.compile(r"sed\s+-i|\bpatch\b|\.write\(|open\([^)]*['\"][wa]", re.I),
     "in-place edit or file write"),
]

_PROFILE_PATH = re.compile(r"/profiles/([A-Za-z0-9_-]+)/")


def _path_facets(text: str, agent_key: Optional[str]) -> List[str]:
    """Facets derived from a filesystem path rather than a command verb."""
    facets: List[str] = []
    if re.search(r"\.env\b|credentials|id_rsa|id_ed25519|\.pem\b", text, re.I):
        facets.append(FACET_CREDENTIAL)
    # Reaching into another agent's profile directory. Observed: an agent denied under
    # its own identity read a peer's .env and acted as that peer, which is identity
    # substitution rather than tool substitution and is worth its own act.
    if agent_key:
        for match in _PROFILE_PATH.finditer(text):
            if match.group(1).lower() != agent_key.lower():
                facets.append(FACET_CROSS_PROFILE)
                break
    return facets


def derive_facets(
    tool_name: str,
    args: Optional[Dict],
    agent_key: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """Return (facets, notes) for a call.

    `facets` is sorted and deduplicated so the same call always derives the same acts —
    a decision that varies by dict ordering is not a decision.

    `notes` explains the outcome and is carried into the decision reasons. It is never
    empty when narrowing failed, so "this did not narrow" is always visible in the trail
    rather than being indistinguishable from "this had nothing to narrow".
    """
    base = (tool_name or "").strip()
    if base not in DERIVABLE_ACTS:
        return [], []

    if not isinstance(args, dict) or not args:
        # The host did not supply arguments. Not an error — some hooks do not pass them —
        # but it means every derived act is unreachable, so it must be recorded.
        return [], ["args_unavailable"]

    facets: List[str] = []
    notes: List[str] = []

    command = _first_str(args, _COMMAND_KEYS)
    if command:
        for facet, pattern, _mechanism in _COMMAND_PATTERNS:
            if facet not in facets and pattern.search(command):
                facets.append(facet)
        facets.extend(f for f in _path_facets(command, agent_key) if f not in facets)

    path = _first_str(args, _PATH_KEYS)
    if path:
        for facet in _path_facets(path, agent_key):
            if facet not in facets:
                facets.append(facet)
        if base in ("write_file", "patch") and FACET_WRITE not in facets:
            facets.append(FACET_WRITE)
        if base == "read_file" and FACET_READ not in facets:
            facets.append(FACET_READ)

    if not facets:
        # Classified as nothing. Distinct from args_unavailable: we saw the arguments and
        # recognised no facet, which may mean the pattern set has a gap.
        notes.append("unclassified" if (command or path) else "args_unavailable")

    return sorted(set(facets)), notes


def dispatched_call(
    tool_name: str, args: Optional[Dict]
) -> Tuple[Optional[str], Optional[Dict]]:
    """Return (act, args) for the act a dispatcher actually invokes, else (None, None).

    Unwrapping, not narrowing. The result is a real act name, so it is evaluated as one rather
    than as a facet of the wrapper — `tool_call{name: email_send_now}` is judged as
    `email_send_now`.

    Returns `("<unresolved dispatch>", None)` when the call is a dispatcher whose target cannot
    be read. That is deliberately not `None`: a dispatch nobody can attribute is the one call
    the policy definitely cannot govern, and it must reach the decision trail rather than pass
    as an ordinary act. It will not match any policy entry, so it denies as unknown — which is
    the correct answer to "run something, I won't say what".
    """
    base = (tool_name or "").strip()
    if base not in DISPATCH_ACTS:
        return None, None
    if not isinstance(args, dict):
        return UNRESOLVED_DISPATCH, None

    inner = next(
        (args[k] for k in _DISPATCH_NAME_KEYS if isinstance(args.get(k), str) and args[k].strip()),
        None,
    )
    if not inner:
        return UNRESOLVED_DISPATCH, None

    inner_args = next(
        (args[k] for k in _DISPATCH_ARG_KEYS if isinstance(args.get(k), dict)), None
    )
    return inner.strip(), inner_args


def candidate_acts(tool_name: str, facets: Sequence[str]) -> List[str]:
    """Derived act names for a call, e.g. ['terminal.network', 'terminal.write'].

    Order is stable but carries no precedence: the caller must evaluate every candidate
    present in the policy and apply the most restrictive result.
    """
    base = (tool_name or "").strip()
    if not base or not facets:
        return []
    return [f"{base}.{facet}" for facet in facets]
