"""Tests for what the PEP returns to Hermes.

The defect this slice fixes: the gate returned {"action": "block"} for `deny` and
`require_approval` alike, flattening a three-valued decision onto two-valued
enforcement — so approval-flagged acts behaved as hard denials and every one
cost a policy-edit-publish-wait cycle.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _load_gate():
    """The plugin lives in a hyphenated directory, so it cannot be imported by name.
    Load __init__.py by path; it has no import-time side effects."""
    path = Path(__file__).resolve().parent / "__init__.py"
    spec = importlib.util.spec_from_file_location("pf_gate_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


class FakePdp:
    def __init__(self, decision: str, *, inline_approval=None, tier="efficiency"):
        self.agent_key = "alex"
        self._decision = decision
        self.refresh_calls = 0
        payload = {"version": "v1"}
        if inline_approval is not None:
            payload["inline_approval"] = inline_approval
        self.bundle = {"payload": payload}
        self._tier = tier

    def refresh(self):
        self.refresh_calls += 1
        return {"pack_version": "p1", "bundle_version": "v1", "state": "normal"}

    def evaluate(self, tool_name, correlation_id=None):  # noqa: ARG002
        return {
            "decision": self._decision,
            "tier": self._tier,
            "requires_approval": self._decision != "allow",
            "reasons": [f"tier:{self._tier}"],
            "bundle_version": "v1",
            "pdp_state": "normal",
        }


class FlipToAllowPdp(FakePdp):
    """A policy fix that landed just before the call — the case the whole
    revalidate-before-blocking step exists for."""

    def __init__(self):
        super().__init__("deny")

    def refresh(self):
        self._decision = "allow"
        return super().refresh()


class GateDirectiveTests(unittest.TestCase):
    def tearDown(self):
        gate._pdp = None

    def _run(self, pdp, tool="email.send_now"):
        gate._pdp = pdp
        return gate.pre_tool_call(tool_name=tool, task_id="corr-1")

    def test_allow_returns_no_directive(self):
        self.assertIsNone(self._run(FakePdp("allow")))

    def test_deny_blocks_even_when_inline_approval_is_enabled(self):
        # Approving an act policy refuses outright is privilege escalation, and is
        # deliberately a separate mechanism with its own binding and expiry.
        result = self._run(FakePdp("deny", inline_approval=True))
        self.assertEqual(result["action"], "block")

    def test_require_approval_blocks_when_agent_not_eligible(self):
        # Default-off: unchanged behaviour until PromptForge publishes eligibility.
        result = self._run(FakePdp("require_approval"))
        self.assertEqual(result["action"], "block")

    def test_require_approval_escalates_when_agent_is_eligible(self):
        result = self._run(FakePdp("require_approval", inline_approval=True))
        self.assertEqual(result["action"], "approve")
        self.assertIn("Approval needed", result["message"])

    def test_escalation_uses_category_grain_so_one_answer_clears_the_chain(self):
        result = self._run(FakePdp("require_approval", inline_approval=True))
        self.assertEqual(result["rule_key"], "email.write")

    def test_domainless_act_falls_back_to_the_act_itself(self):
        result = self._run(
            FakePdp("require_approval", inline_approval=True), tool="hermes_native_act"
        )
        self.assertEqual(result["rule_key"], "hermes_native_act")

    def test_denial_revalidates_once_before_refusing(self):
        pdp = FakePdp("deny")
        self._run(pdp)
        self.assertEqual(pdp.refresh_calls, 1)

    def test_allow_does_not_spend_a_revalidation(self):
        pdp = FakePdp("allow")
        self._run(pdp)
        self.assertEqual(pdp.refresh_calls, 0)

    def test_policy_fix_that_already_landed_is_honoured_immediately(self):
        pdp = FlipToAllowPdp()
        self.assertIsNone(self._run(pdp))
        self.assertEqual(pdp.refresh_calls, 1)

    def test_revalidation_failure_keeps_the_original_decision(self):
        pdp = FakePdp("deny")
        pdp.refresh = lambda: (_ for _ in ()).throw(RuntimeError("network down"))
        result = self._run(pdp)
        self.assertEqual(result["action"], "block")

    def test_missing_tool_name_blocks(self):
        gate._pdp = FakePdp("allow")
        result = gate.pre_tool_call(tool_name="")
        self.assertEqual(result["action"], "block")


if __name__ == "__main__":
    unittest.main()
