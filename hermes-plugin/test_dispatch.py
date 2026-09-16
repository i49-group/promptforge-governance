"""
Tests for unwrapping dispatcher acts.

The case that motivated these: an agent's policy gated `email_send_now` behind approval, and
every send the agent had ever made went through `tool_call` instead — so the gate was published,
correct, and unreachable. A test suite that only calls acts by their own name cannot see that,
which is the same blind spot that hid the category-grain bug (P-116) and the package-import bug
(P-115). So the assertions here are about what the gate *receives*, not what the policy says.
"""

import unittest

try:
    from .derive import UNRESOLVED_DISPATCH, dispatched_call
    from .pdp import evaluate_against_bundle, evaluate_derived
except ImportError:  # loaded flat rather than as a package
    from derive import UNRESOLVED_DISPATCH, dispatched_call  # type: ignore
    from pdp import evaluate_against_bundle, evaluate_derived  # type: ignore


def bundle(tools, default_tier="velocity"):
    return {
        "default_tier": default_tier,
        "tools": tools,
        "tool_categories": {},
        "version": "test",
        "etag": "test",
    }


ALLOW = {"granted": True, "tier": "velocity", "requires_approval": False}
GATED = {"granted": True, "tier": "control", "requires_approval": True}
REFUSED = {"granted": False, "tier": "control", "requires_approval": True}


class DispatchedCallTest(unittest.TestCase):
    def test_reads_the_invoked_act_and_its_arguments(self):
        act, args = dispatched_call(
            "tool_call",
            {"name": "mcp__example__email_send_now", "arguments": {"campaignId": "abc"}},
        )
        self.assertEqual(act, "mcp__example__email_send_now")
        self.assertEqual(args, {"campaignId": "abc"})

    def test_ordinary_act_is_not_a_dispatch(self):
        self.assertEqual(dispatched_call("terminal", {"command": "ls"}), (None, None))

    def test_dispatch_with_no_readable_target_is_named_not_ignored(self):
        # Returning None here would let the call be judged as plain `tool_call`, i.e. the one
        # call we certainly cannot attribute would be the one we wave through.
        for args in ({}, None, {"arguments": {"x": 1}}, {"name": "   "}, {"name": 42}):
            act, _ = dispatched_call("tool_call", args)
            self.assertEqual(act, UNRESOLVED_DISPATCH, f"args={args!r}")


class UnwrappingTest(unittest.TestCase):
    def test_gate_on_the_invoked_act_fires_through_the_wrapper(self):
        """The bug this exists for: a gate the agent walked past by wrapping the call."""
        result = evaluate_derived(
            bundle({"tool_call": ALLOW, "email_send_now": GATED}),
            "tool_call",
            {"name": "email_send_now", "arguments": {}},
        )
        self.assertEqual(result["decision"], "require_approval")
        self.assertIn("dispatched_act:email_send_now", result["reasons"])
        self.assertIn("decided_on:dispatched", result["reasons"])

    def test_refusal_on_the_invoked_act_is_not_escaped_by_wrapping(self):
        result = evaluate_derived(
            bundle({"tool_call": ALLOW, "email_update_template": REFUSED}),
            "tool_call",
            {"name": "email_update_template"},
        )
        self.assertEqual(result["decision"], "deny")

    def test_a_refused_wrapper_still_denies_so_deploying_this_loosens_nothing(self):
        """Every governed policy today refuses the wrapper. This must not change them."""
        result = evaluate_derived(
            bundle({"tool_call": REFUSED, "read_file": ALLOW}),
            "tool_call",
            {"name": "read_file"},
        )
        self.assertEqual(result["decision"], "deny")
        self.assertIn("decided_on:dispatcher", result["reasons"])

    def test_an_act_the_policy_never_listed_denies_rather_than_riding_the_wrapper(self):
        result = evaluate_derived(
            bundle({"tool_call": ALLOW}), "tool_call", {"name": "some_unlisted_act"}
        )
        self.assertEqual(result["decision"], "deny")
        self.assertIn("unknown_tool", " ".join(result["reasons"]))

    def test_unattributable_dispatch_denies(self):
        result = evaluate_derived(bundle({"tool_call": ALLOW}), "tool_call", {})
        self.assertEqual(result["decision"], "deny")
        self.assertEqual(result["dispatched_act"], UNRESOLVED_DISPATCH)

    def test_a_dispatched_shell_command_still_derives_its_own_facets(self):
        """Unwrapping composes with narrowing; the inner act is not treated as opaque."""
        result = evaluate_derived(
            bundle({"tool_call": ALLOW, "terminal": ALLOW, "terminal.network": GATED}),
            "tool_call",
            {"name": "terminal", "arguments": {"command": "curl https://example.com"}},
        )
        self.assertEqual(result["decision"], "require_approval")
        self.assertIn("derived_act:terminal.network", result["reasons"])

    def test_unwrapping_can_only_tighten(self):
        """
        The property that makes this safe to deploy to a live fleet: for every combination of
        wrapper and invoked-act policy, unwrapping is never more permissive than the behaviour
        it replaces — judging the wrapper's name alone, which is what shipped before.
        """
        rank = {"allow": 0, "require_approval": 1, "deny": 2}
        for wrapper in (ALLOW, GATED, REFUSED):
            for inner in (ALLOW, GATED, REFUSED, None):
                tools = {"tool_call": wrapper}
                if inner is not None:
                    tools["some_act"] = inner
                payload = bundle(tools)
                wrapped = evaluate_derived(payload, "tool_call", {"name": "some_act"})
                before = evaluate_against_bundle(payload, "tool_call")
                self.assertGreaterEqual(
                    rank[wrapped["decision"]],
                    rank[before["decision"]],
                    f"unwrapping loosened: wrapper={wrapper} inner={inner}",
                )

    def test_a_dispatcher_the_host_sends_without_arguments_denies(self):
        """
        The one behaviour change on deploy, stated as a test so it is a decision rather than a
        surprise. Before, a dispatcher with no arguments was judged on its own name and could be
        allowed. Now it denies, because a dispatch whose target is unknown is precisely the call
        that cannot be governed. Hermes does pass arguments to the hook — verified in production
        by derived shell facets appearing in live decision records — so this affects no current
        traffic, but a host that omits them will fail closed rather than open.
        """
        payload = bundle({"tool_call": ALLOW})
        self.assertEqual(evaluate_against_bundle(payload, "tool_call")["decision"], "allow")
        self.assertEqual(evaluate_derived(payload, "tool_call", None)["decision"], "deny")


if __name__ == "__main__":
    unittest.main()
