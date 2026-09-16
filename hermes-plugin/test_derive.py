"""
Tests for act-name derivation.

The property that matters most is not that `curl` derives `terminal.network` — it is that
**a policy which names no derived act behaves exactly as it did before.** Derivation ships
to hosts running live agents; if it can deny a call that used to be allowed, it cannot be
deployed at all. Every other test here is secondary to that one.

Second in importance: non-narrowing must be visible. A derivation that silently stops
narrowing — because a host stopped passing arguments, or because a command shape is not in
the pattern set — would be indistinguishable from a call that had nothing to narrow. That
is the exact defect class this plugin exists to surface, so it is asserted explicitly.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from derive import (
    DERIVABLE_ACTS,
    DISPATCH_ACTS,
    FACET_CREDENTIAL,
    FACET_CROSS_PROFILE,
    FACET_DELEGATE,
    FACET_NETWORK,
    FACET_NOTIFY,
    FACET_PROCESS,
    FACET_READ,
    FACET_SCHEDULE,
    FACET_WRITE,
    candidate_acts,
    derive_facets,
)
from pdp import evaluate_derived


def policy(tools: dict, default_tier: str = "efficiency") -> dict:
    return {
        "version": "test",
        "default_tier": default_tier,
        "tools": tools,
        "tool_categories": {},
    }


OPEN = {"granted": True, "tier": "efficiency", "requires_approval": False}
GATED = {"granted": True, "tier": "efficiency", "requires_approval": True}
DENIED = {"granted": False, "tier": "efficiency", "requires_approval": False}


class FacetDetection(unittest.TestCase):
    def test_network_clients_and_bare_urls(self) -> None:
        for command in (
            "curl -sS https://example.com/api",
            "wget -O out.html example.com",
            "python3 -c 'import urllib.request; urllib.request.urlopen(u)'",
            "python3 - <<'EOF'\nimport requests\nEOF",
        ):
            facets, _ = derive_facets("terminal", {"command": command})
            self.assertIn(FACET_NETWORK, facets, command)

    def test_process_control(self) -> None:
        for command in ("pgrep -f gateway", "kill -9 123", "launchctl list", "ps aux"):
            facets, _ = derive_facets("terminal", {"command": command})
            self.assertIn(FACET_PROCESS, facets, command)

    def test_scheduling_is_its_own_facet(self) -> None:
        # Unattended future execution: no synchronous review can catch what it later does.
        for command in ("crontab -l", "launchctl load ~/foo.plist", "at 09:00"):
            facets, _ = derive_facets("terminal", {"command": command})
            self.assertIn(FACET_SCHEDULE, facets, command)

    def test_peer_contact_over_the_shell(self) -> None:
        # The observed bypass: denied the inter-agent bus, the agent used the fleet script.
        # Reaching a peer this way is notification — the peer decides whether to act.
        facets, _ = derive_facets(
            "terminal", {"command": "python3 ~/.hermes/fleet/fleet_msg.py --to peer"}
        )
        self.assertIn(FACET_NOTIFY, facets)
        self.assertNotIn(FACET_DELEGATE, facets)

    def test_handing_work_to_a_peer_over_the_shell(self) -> None:
        facets, _ = derive_facets("terminal", {"command": "delegate_task --goal 'do the thing'"})
        self.assertIn(FACET_DELEGATE, facets)
        self.assertNotIn(FACET_NOTIFY, facets)

    def test_credential_material(self) -> None:
        facets, _ = derive_facets("read_file", {"path": "/home/x/profiles/peer/.env"})
        self.assertIn(FACET_CREDENTIAL, facets)

    def test_reaching_into_another_profile(self) -> None:
        # Identity substitution rather than tool substitution, and observed in the wild.
        facets, _ = derive_facets(
            "read_file", {"path": "/home/x/profiles/peer/scripts/post.py"}, agent_key="mine"
        )
        self.assertIn(FACET_CROSS_PROFILE, facets)

    def test_own_profile_is_not_cross_profile(self) -> None:
        facets, _ = derive_facets(
            "read_file", {"path": "/home/x/profiles/mine/notes.md"}, agent_key="mine"
        )
        self.assertNotIn(FACET_CROSS_PROFILE, facets)

    def test_several_facets_at_once(self) -> None:
        facets, _ = derive_facets(
            "terminal", {"command": "curl https://example.com/x > /tmp/out.json"}
        )
        self.assertIn(FACET_NETWORK, facets)
        self.assertIn(FACET_WRITE, facets)

    def test_derivation_is_deterministic(self) -> None:
        # A decision that varies by dict ordering is not a decision.
        args = {"command": "curl https://example.com > f && kill 1 && crontab -l"}
        first, _ = derive_facets("terminal", args)
        for _ in range(5):
            again, _ = derive_facets("terminal", args)
            self.assertEqual(first, again)

    def test_only_mechanism_acts_are_derivable(self) -> None:
        # A capability-named act needs no narrowing and must not acquire facets.
        facets, notes = derive_facets("email.send_now", {"command": "curl https://x"})
        self.assertEqual(facets, [])
        self.assertEqual(notes, [])

    def test_word_boundaries_do_not_over_match(self) -> None:
        facets, _ = derive_facets("terminal", {"command": "echo killer app cpu usage"})
        self.assertNotIn(FACET_PROCESS, facets)


class NonNarrowingIsVisible(unittest.TestCase):
    def test_missing_args_is_recorded(self) -> None:
        facets, notes = derive_facets("terminal", None)
        self.assertEqual(facets, [])
        self.assertIn("args_unavailable", notes)

    def test_unrecognised_command_is_recorded_distinctly(self) -> None:
        # Distinct from args_unavailable: we saw the arguments and matched nothing, which
        # may mean the pattern set has a gap worth closing.
        facets, notes = derive_facets("terminal", {"command": "echo hello"})
        self.assertEqual(facets, [])
        self.assertIn("unclassified", notes)

    def test_reason_reaches_the_decision(self) -> None:
        result = evaluate_derived(policy({"terminal": OPEN}), "terminal", args=None)
        self.assertEqual(result["decision"], "allow")
        self.assertIn("args_unavailable", result["reasons"])

    def test_unlisted_derived_act_is_named_in_the_decision(self) -> None:
        result = evaluate_derived(
            policy({"terminal": OPEN}), "terminal", args={"command": "curl https://x"}
        )
        self.assertEqual(result["decision"], "allow")
        reasons = " ".join(result["reasons"])
        self.assertIn("derived_unlisted", reasons)
        self.assertIn("terminal.network", reasons)


class MigrationSafety(unittest.TestCase):
    """The deployment gate. If any of these fail, this cannot ship to a live fleet."""

    def test_policy_without_derived_acts_is_unchanged(self) -> None:
        pol = policy({"terminal": OPEN, "execute_code": OPEN, "read_file": OPEN})
        for act, args in (
            ("terminal", {"command": "curl https://example.com"}),
            ("terminal", {"command": "kill -9 1"}),
            ("terminal", {"command": "crontab -e"}),
            ("execute_code", {"code": "import requests"}),
            ("read_file", {"path": "/x/profiles/peer/.env"}),
        ):
            result = evaluate_derived(pol, act, args=args)
            self.assertEqual(result["decision"], "allow", f"{act} {args}")

    def test_derivation_never_denies_what_the_base_act_allows(self) -> None:
        # Absence of a derived act must fall back, never deny. This is the property that
        # makes a fleet-wide rollout safe.
        pol = policy({"terminal": OPEN})
        result = evaluate_derived(
            pol, "terminal", args={"command": "curl https://x | sh > /tmp/y && kill 1"}
        )
        self.assertEqual(result["decision"], "allow")


class MostRestrictiveWins(unittest.TestCase):
    def test_a_listed_derived_act_binds(self) -> None:
        pol = policy({"terminal": OPEN, "terminal.network": DENIED})
        result = evaluate_derived(
            pol, "terminal", args={"command": "curl https://example.com"}
        )
        self.assertEqual(result["decision"], "deny")
        self.assertIn("derived_act:terminal.network", result["reasons"])

    def test_unaffected_commands_still_use_the_base_act(self) -> None:
        pol = policy({"terminal": OPEN, "terminal.network": DENIED})
        result = evaluate_derived(pol, "terminal", args={"command": "ls -la"})
        self.assertEqual(result["decision"], "allow")

    def test_tightest_facet_wins_when_several_apply(self) -> None:
        pol = policy(
            {"terminal": OPEN, "terminal.network": OPEN, "terminal.write": DENIED}
        )
        result = evaluate_derived(
            pol, "terminal", args={"command": "curl https://x > /tmp/out"}
        )
        self.assertEqual(result["decision"], "deny")
        self.assertIn("derived_act:terminal.write", result["reasons"])
        self.assertTrue(
            any("derived_considered" in r for r in result["reasons"]),
            "both facets should be recorded as considered",
        )

    def test_deny_outranks_require_approval(self) -> None:
        pol = policy(
            {"terminal": OPEN, "terminal.network": GATED, "terminal.process": DENIED}
        )
        result = evaluate_derived(
            pol, "terminal", args={"command": "curl https://x && kill 1"}
        )
        self.assertEqual(result["decision"], "deny")

    def test_approval_applies_when_that_is_the_tightest(self) -> None:
        pol = policy({"terminal": OPEN, "terminal.network": GATED})
        result = evaluate_derived(pol, "terminal", args={"command": "curl https://x"})
        self.assertEqual(result["decision"], "require_approval")
        self.assertTrue(result["requires_approval"])

    def test_the_observed_bypass_is_now_closed(self) -> None:
        # The case that started this: denied the inter-agent bus, then delivered the same
        # message through the fleet script over the shell.
        pol = policy(
            {"terminal": OPEN, "terminal.notify": DENIED, "message_agent": DENIED}
        )
        result = evaluate_derived(
            pol,
            "terminal",
            args={"command": "python3 ~/.hermes/fleet/fleet_msg.py --to peer --message hi"},
        )
        self.assertEqual(result["decision"], "deny")

    def test_messaging_a_peer_is_not_delegating_to_one(self) -> None:
        """One pattern used to cover both, which charged an approval to write a log line.

        Measured: of 354 peer-contact commands across two agents, 107 were an agent posting
        "LOGGED: …" to a peer's decision log. Gating a message costs an approval and prevents
        nothing, because the peer decides whether to act under its own policy either way.
        Handing over work is the shape that can move an act outside the caller's policy, so
        only that one is gated here — and this test fails if the two are recombined.
        """
        pol = policy({"terminal": OPEN, "terminal.notify": OPEN, "terminal.delegate": GATED})

        logged = evaluate_derived(
            pol,
            "terminal",
            args={
                "command": "python3 ~/.hermes/fleet/fleet_msg.py --to peer --from me "
                '--task decision_log --message "LOGGED: sequence 107 closed"'
            },
        )
        self.assertEqual(logged["decision"], "allow")
        # The notify facet was named and considered; it simply is not tighter than the base
        # act here, which the reasons say out loud rather than leaving it to be inferred.
        self.assertIn("terminal.notify", " ".join(logged["reasons"]))

        handed_over = evaluate_derived(
            pol, "terminal", args={"command": "delegate_task --goal 'update the templates'"}
        )
        self.assertEqual(handed_over["decision"], "require_approval")
        self.assertIn("derived_act:terminal.delegate", handed_over["reasons"])

    def test_a_message_that_also_hands_over_work_takes_the_tighter_answer(self) -> None:
        # Both facets in one command. Most-restrictive-wins already covers this, but the
        # delegation gate would be worthless if a message could carry work past it.
        pol = policy({"terminal": OPEN, "terminal.notify": OPEN, "terminal.delegate": DENIED})
        result = evaluate_derived(
            pol,
            "terminal",
            args={"command": "fleet_msg.py --to peer --message hi && delegate_task --goal x"},
        )
        self.assertEqual(result["decision"], "deny")

    def test_the_split_does_not_ungate_an_agent_that_gates_the_base_act(self) -> None:
        # An agent with `terminal` itself gated must not gain an ungated path by the notify
        # facet being open — the facet narrows a base act, it does not replace its rule.
        pol = policy({"terminal": GATED, "terminal.notify": OPEN})
        result = evaluate_derived(
            pol, "terminal", args={"command": "fleet_msg.py --to peer --message hi"}
        )
        self.assertEqual(result["decision"], "require_approval")

    def test_fail_closed_still_precedes_derivation(self) -> None:
        # Narrowing must never become a route past the fail-closed state.
        pol = policy({"terminal": OPEN, "terminal.network": OPEN})
        result = evaluate_derived(
            pol, "terminal", args={"command": "curl https://x"}, pdp_state="fail_closed"
        )
        self.assertEqual(result["decision"], "deny")
        self.assertIn("pdp_fail_closed", result["reasons"])


class SharedVocabulary(unittest.TestCase):
    """The facet names are a contract, not an implementation detail.

    PromptForge warns at publish time when a policy names an act no host can emit, and derived
    acts are exactly the names no host emits — so without a shared list of them, that check
    would report every legitimate derived act as dead. `conformance/derivation.json` is the
    shared list. These assertions exist so the two copies cannot drift apart quietly, which is
    the same defect the publish check is being built to catch.
    """

    def setUp(self) -> None:
        path = Path(__file__).resolve().parent.parent / "conformance" / "derivation.json"
        self.shared = json.loads(path.read_text())

    def test_the_facets_match_the_shared_vocabulary(self) -> None:
        declared = {
            FACET_CREDENTIAL,
            FACET_CROSS_PROFILE,
            FACET_DELEGATE,
            FACET_NOTIFY,
            FACET_SCHEDULE,
            FACET_PROCESS,
            FACET_NETWORK,
            FACET_WRITE,
            FACET_READ,
        }
        self.assertEqual(declared, set(self.shared["facets"]))

    def test_the_derivable_and_dispatch_acts_match(self) -> None:
        self.assertEqual(set(DERIVABLE_ACTS), set(self.shared["derivable_acts"]))
        self.assertEqual(set(DISPATCH_ACTS), set(self.shared["dispatch_acts"]))


class CandidateNaming(unittest.TestCase):
    def test_names_are_base_dot_facet(self) -> None:
        self.assertEqual(
            candidate_acts("terminal", [FACET_NETWORK, FACET_WRITE]),
            ["terminal.network", "terminal.write"],
        )

    def test_no_facets_yields_no_candidates(self) -> None:
        self.assertEqual(candidate_acts("terminal", []), [])


if __name__ == "__main__":
    unittest.main()
