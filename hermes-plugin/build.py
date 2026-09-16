"""The build identity of this enforcement point.

Why this exists (P-121): the policy pack is versioned, content-hashed, revalidated by ETag,
given a TTL and a grace window, and fails closed when it cannot be refreshed — and none of that
machinery says anything about *the code that reads it*. A gateway once ran three files behind
while every one of those layers reported healthy: it fetched the newest pack, verified its
signature, honoured its TTL, and then applied old logic to it. Perfect delivery, inert control.
The drift was not undetected, it was undetectable, because nothing the host sent identified which
build was enforcing.

The identifier is a **content hash of this plugin's own runtime sources**, not a hand-maintained
version string. A version someone must remember to bump is a control that fails silently the
first time they forget, which is the defect class this whole module belongs to. The hash changes
when and only when the enforcing code changes, with no human step.

`VERSION` is kept alongside it for humans reading a log, and is deliberately not the thing the
standing check compares.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

VERSION = "1.2.0"

# Runtime modules only. Tests are excluded because they do not enforce anything, and including
# them would report a new build for a change that cannot alter a decision.
_RUNTIME_MODULES = (
    "__init__.py",
    "actname.py",
    "build.py",
    "derive.py",
    "messages.py",
    "pdp.py",
    "reporter.py",
)

_UNKNOWN = "unknown"


def _compute_build() -> str:
    """Hash the runtime sources. Returns "unknown" rather than raising.

    A plugin that cannot identify itself must still enforce policy — refusing to load over a
    missing file here would convert an observability gap into an outage, which is a strictly
    worse trade. The standing check treats "unknown" as a finding, so this degrades to the
    problem being reported rather than hidden.
    """
    here = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in _RUNTIME_MODULES:
        path = here / name
        try:
            digest.update(name.encode("utf-8"))
            digest.update(path.read_bytes())
        except OSError:
            return _UNKNOWN
    return digest.hexdigest()[:12]


BUILD = _compute_build()


def build_headers() -> dict:
    """Headers identifying this build, sent on every governance fetch.

    Carried on the fetch rather than only on decision reports because the fetch is the one
    exchange every governed gateway always makes: reporting is per-agent opt-in and plain allows
    are sampled, so decisions are a channel that can legitimately be silent. A build that never
    reports is the signal that matters most (an agent running ungoverned looks exactly like an
    agent having a quiet day), and it is only trustworthy on a channel that cannot be switched off.
    """
    return {
        "X-PromptForge-Pep-Build": BUILD,
        "X-PromptForge-Pep-Version": VERSION,
    }
