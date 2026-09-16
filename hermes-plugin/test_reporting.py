"""Tests for reporting local PDP decisions back to PromptForge.

The defect this closes: the `POST /api/governance/decisions` endpoint previously had
no caller, so `governance_decisions` held zero rows and "no denials happened" was
indistinguishable from "the emitter never fires". A receiver with no sender is a
dead control.

Two invariants are load-bearing and tested here rather than assumed:
  - reporting is silent unless the agent's policy opted in
  - reporting can never delay, fail or alter a tool call
"""

from __future__ import annotations

import importlib.util
import io
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load("pf_gate_reporting_under_test", "__init__.py")
reporter_mod = _load("pf_reporter_under_test", "reporter.py")


class FakeReporter:
    def __init__(self):
        self.calls: list[dict] = []

    def report(self, **kwargs):
        self.calls.append(kwargs)
        return True


class ExplodingReporter:
    def report(self, **kwargs):
        raise RuntimeError("reporting backend is down")


class FakePdp:
    def __init__(self, decision: str, *, report_decisions=None, inline_approval=None):
        self.agent_key = "alex"
        self.base_url = "https://pf.test"
        self.token = "tok"
        self.environment = "production"
        self.bundle_version = "2026.09.15.abc"
        self._decision = decision
        payload = {"version": "v1"}
        if report_decisions is not None:
            payload["report_decisions"] = report_decisions
        if inline_approval is not None:
            payload["inline_approval"] = inline_approval
        self.bundle = {"payload": payload}

    def refresh(self):
        return {"pack_version": "p1", "bundle_version": "v1", "state": "normal"}

    # Signature must track GovernancePdp.evaluate, including `args`. The hook passes
    # arguments so the act name can be narrowed; a fake that omits them raises
    # TypeError inside the hook's own except clause and reports as a gate failure.
    def evaluate(self, tool_name, correlation_id=None, args=None):  # noqa: ARG002
        return {
            "decision": self._decision,
            "tier": "efficiency",
            "requires_approval": self._decision != "allow",
            "reasons": ["tier:efficiency"],
            "bundle_version": "v1",
            "correlation_id": "corr-1",
            "pdp_state": "normal",
        }


class ReportingOptInTests(unittest.TestCase):
    def setUp(self):
        self.sink = FakeReporter()
        gate._reporter = self.sink
        gate._pending_escalations.clear()

    def tearDown(self):
        gate._pdp = None
        gate._reporter = None
        gate._pending_escalations.clear()

    def _run(self, pdp, tool="email.send_now", **kwargs):
        gate._pdp = pdp
        return gate.pre_tool_call(tool_name=tool, task_id="task-1", **kwargs)

    def test_silent_when_policy_has_not_opted_in(self):
        self._run(FakePdp("allow"))
        self.assertEqual(self.sink.calls, [])

    def test_silent_when_opt_in_is_explicitly_false(self):
        self._run(FakePdp("allow", report_decisions=False))
        self.assertEqual(self.sink.calls, [])

    def test_allow_is_reported_when_opted_in(self):
        self._run(FakePdp("allow", report_decisions=True))
        self.assertEqual(len(self.sink.calls), 1)
        self.assertEqual(self.sink.calls[0]["decision"], "allow")
        self.assertEqual(self.sink.calls[0]["tool_name"], "email.send_now")
        self.assertEqual(self.sink.calls[0]["agent_key"], "alex")

    def test_deny_is_reported_as_deny(self):
        self._run(FakePdp("deny", report_decisions=True))
        self.assertEqual(len(self.sink.calls), 1)
        self.assertEqual(self.sink.calls[0]["decision"], "deny")
        self.assertIn("blocked_by_host", self.sink.calls[0]["reasons"])

    def test_escalation_is_reported_as_require_approval_not_as_allow(self):
        # The whole point of the slice: a human-gated act must not be recorded as
        # though policy simply permitted it.
        self._run(FakePdp("require_approval", report_decisions=True, inline_approval=True))
        self.assertEqual(len(self.sink.calls), 1)
        self.assertEqual(self.sink.calls[0]["decision"], "require_approval")
        self.assertIn("escalated_to_host_gate", self.sink.calls[0]["reasons"])

    def test_gated_act_with_no_eligibility_is_reported_as_require_approval(self):
        self._run(FakePdp("require_approval", report_decisions=True))
        self.assertEqual(self.sink.calls[0]["decision"], "require_approval")
        self.assertIn("blocked_by_host", self.sink.calls[0]["reasons"])

    def test_policy_version_travels_with_the_decision(self):
        self._run(FakePdp("deny", report_decisions=True))
        self.assertEqual(self.sink.calls[0]["policy_version"], "2026.09.15.abc")


class ApprovalOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.sink = FakeReporter()
        gate._reporter = self.sink
        gate._pending_escalations.clear()

    def tearDown(self):
        gate._pdp = None
        gate._reporter = None
        gate._pending_escalations.clear()

    def test_act_that_ran_after_escalation_is_reported_as_human_approved(self):
        pdp = FakePdp("require_approval", report_decisions=True, inline_approval=True)
        gate._pdp = pdp
        gate.pre_tool_call(
            tool_name="email.send_now", task_id="task-1", tool_call_id="call-9"
        )
        gate.post_tool_call(
            tool_name="email.send_now", task_id="task-1", tool_call_id="call-9"
        )

        self.assertEqual(len(self.sink.calls), 2)
        outcome = self.sink.calls[1]
        self.assertEqual(outcome["decision"], "allow")
        self.assertIn("human_approved", outcome["reasons"])
        self.assertIn("ran", outcome["reasons"])

    def test_declined_approval_leaves_only_the_require_approval_row(self):
        # Hermes never reaches the tool, so post_tool_call never fires. The absence
        # of a following run is the only evidence a decline leaves.
        pdp = FakePdp("require_approval", report_decisions=True, inline_approval=True)
        gate._pdp = pdp
        gate.pre_tool_call(
            tool_name="email.send_now", task_id="task-1", tool_call_id="call-9"
        )
        self.assertEqual(len(self.sink.calls), 1)
        self.assertEqual(self.sink.calls[0]["decision"], "require_approval")

    def test_plain_allow_is_not_double_reported_after_it_runs(self):
        pdp = FakePdp("allow", report_decisions=True)
        gate._pdp = pdp
        gate.pre_tool_call(
            tool_name="email.get_inbox", task_id="task-1", tool_call_id="call-1"
        )
        gate.post_tool_call(
            tool_name="email.get_inbox", task_id="task-1", tool_call_id="call-1"
        )
        self.assertEqual(len(self.sink.calls), 1)

    def test_post_tool_call_for_an_unknown_call_reports_nothing(self):
        gate._pdp = FakePdp("allow", report_decisions=True)
        gate.post_tool_call(tool_name="email.send_now", tool_call_id="never-seen")
        self.assertEqual(self.sink.calls, [])

    def test_pending_escalations_are_bounded(self):
        pdp = FakePdp("require_approval", report_decisions=True, inline_approval=True)
        gate._pdp = pdp
        for i in range(gate._PENDING_MAX + 10):
            gate.pre_tool_call(
                tool_name="email.send_now", task_id="t", tool_call_id=f"call-{i}"
            )
        self.assertLessEqual(len(gate._pending_escalations), gate._PENDING_MAX)

    def test_escalation_falls_back_to_task_and_act_without_a_call_id(self):
        pdp = FakePdp("require_approval", report_decisions=True, inline_approval=True)
        gate._pdp = pdp
        gate.pre_tool_call(tool_name="email.send_now", task_id="task-7")
        gate.post_tool_call(tool_name="email.send_now", task_id="task-7")
        self.assertEqual(len(self.sink.calls), 2)
        self.assertIn("human_approved", self.sink.calls[1]["reasons"])


class ReportingNeverBreaksTheActTests(unittest.TestCase):
    def tearDown(self):
        gate._pdp = None
        gate._reporter = None
        gate._pending_escalations.clear()

    def test_a_failing_reporter_does_not_change_the_directive(self):
        gate._reporter = ExplodingReporter()
        gate._pdp = FakePdp("deny", report_decisions=True)
        result = gate.pre_tool_call(tool_name="email.send_now", task_id="t")
        self.assertEqual(result["action"], "block")

    def test_a_failing_reporter_does_not_block_an_allow(self):
        gate._reporter = ExplodingReporter()
        gate._pdp = FakePdp("allow", report_decisions=True)
        self.assertIsNone(gate.pre_tool_call(tool_name="email.get_inbox", task_id="t"))


class DecisionReporterTests(unittest.TestCase):
    def _reporter(self, **kwargs):
        return reporter_mod.DecisionReporter(
            base_url="https://pf.test", token="tok", **kwargs
        )

    def test_a_full_queue_drops_instead_of_blocking(self):
        r = self._reporter(queue_max=1)
        r._ensure_worker = lambda: None  # nothing drains, so the queue stays full
        self.assertTrue(r.report(agent_key="a", tool_name="t", decision="allow"))
        with self.assertLogs(reporter_mod.logger, level="WARNING") as logged:
            self.assertFalse(r.report(agent_key="a", tool_name="t", decision="allow"))
        self.assertEqual(r.dropped, 1)
        self.assertIn("queue full", "\n".join(logged.output))

    def test_a_transport_failure_is_counted_and_warned_not_raised(self):
        r = self._reporter()
        r._post = lambda payload: (_ for _ in ()).throw(OSError("connection refused"))
        # Asserted rather than allowed to print: the warning is the point (a failure logged at
        # debug was invisible in production, P-125), and an unasserted log line is also test noise.
        with self.assertLogs(reporter_mod.logger, level="WARNING") as logged:
            r.report(agent_key="a", tool_name="t", decision="deny")
            self.assertTrue(r.drain())
        self.assertEqual(r.failed, 1)
        self.assertEqual(r.sent, 0)
        self.assertIn("connection refused", "\n".join(logged.output))

    def test_a_successful_write_is_counted_as_sent(self):
        r = self._reporter()
        body = io.BytesIO(b'{"success":true,"data":{"accepted":true}}')
        body.__enter__ = lambda: body  # type: ignore[method-assign]
        body.__exit__ = lambda *a: None  # type: ignore[method-assign]
        with mock.patch.object(reporter_mod.urllib.request, "urlopen", return_value=body):
            r.report(agent_key="a", tool_name="t", decision="deny")
            self.assertTrue(r.drain())
        self.assertEqual(r.sent, 1)

    def test_accepted_false_is_counted_separately_from_a_failure(self):
        # The endpoint answers 200/accepted=false when the org has not opted in, so
        # "reporting is on but nothing lands" must not look like a healthy write.
        r = self._reporter()
        body = io.BytesIO(
            b'{"success":true,"data":{"accepted":false,"reason":"not_opted_in"}}'
        )
        body.__enter__ = lambda: body  # type: ignore[method-assign]
        body.__exit__ = lambda *a: None  # type: ignore[method-assign]
        with mock.patch.object(reporter_mod.urllib.request, "urlopen", return_value=body):
            # Asserted rather than allowed to print: this now warns, and the warning is the
            # feature — a decision that never reached the record should be visible at the
            # level operators actually read.
            with self.assertLogs(reporter_mod.logger, level="WARNING") as logged:
                r.report(agent_key="a", tool_name="t", decision="deny")
                self.assertTrue(r.drain())
        self.assertEqual(r.rejected, 1)
        self.assertEqual(r.sent, 0)
        self.assertIn("did not record a decision", "\n".join(logged.output))

    def test_the_environment_travels_with_every_report(self):
        r = self._reporter(environment="staging")
        captured: list[dict] = []
        r._post = lambda payload: captured.append(payload)
        r.report(agent_key="a", tool_name="t", decision="allow")
        self.assertTrue(r.drain())
        self.assertEqual(captured[0]["environment"], "staging")


if __name__ == "__main__":
    unittest.main()
