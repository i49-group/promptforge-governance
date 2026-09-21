"""Regenerate `conformance/pep-build.json` from the plugin sources.

Run after changing any enforcing module. `test_build.py::RecordedBuild` fails until you do, which
is the only thing keeping the record honest — see that test for why a stale record is worse than no
record at all.

    pnpm build:record-pep-build

Writes only the fields that are derived. The prose (`why`) is preserved from the existing file so
that regenerating never quietly discards the explanation of why the file exists.

Deliberately NOT inside `hermes-plugin/`. Three separate guards there assert that every module in
that directory is enforcing code a host loads — it is hashed into the build, listed in
`_SIBLINGS`, and held to the dual-mode import rule. All three flagged this file when it briefly
lived there, and they were right to: a build-time tool satisfies none of those contracts, and
widening three guards to admit one script would have made each of them slightly worse at catching
the next genuinely enforcing module added without care.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hermes-plugin"))

import build as build_mod  # noqa: E402  (path must be set first)

RECORD = ROOT / "conformance" / "pep-build.json"


def main() -> int:
    existing = json.loads(RECORD.read_text()) if RECORD.exists() else {}
    was = existing.get("build")

    record = {
        "$comment": "GENERATED — do not hand-edit. Regenerate with: pnpm build:record-pep-build",
        "build": build_mod.BUILD,
        "recorded": date.today().isoformat(),
        "algorithm": existing.get(
            "algorithm",
            "sha256, updated with each module's filename then its bytes, in the order given by "
            "`modules`; first 12 hex characters of the digest",
        ),
        "modules": list(build_mod._RUNTIME_MODULES),
        "why": existing.get("why", ""),
    }
    # ensure_ascii=False: the prose in this file is meant to be read by whoever hits the failing
    # test, and `\u2014` litter makes it read like machine output nobody has to take seriously.
    RECORD.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")

    if was == build_mod.BUILD:
        print(f"unchanged — {build_mod.BUILD}")
    else:
        print(f"recorded {build_mod.BUILD} (was {was or 'nothing'})")
        print("")
        print("Mirror it into PromptForge or its check still compares against the old hash:")
        print("  cp conformance/pep-build.json \\")
        print("     ../Promptforge/promptforge/packages/governance-pdp/conformance/pep-build.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
