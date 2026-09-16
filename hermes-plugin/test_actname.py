"""
Tests for act-name canonicalization.

The defect: policies are written in a dotted `domain.action` form, hosts emit MCP acts as
`mcp__<server>__<domain>_<action>`, and nothing reconciled the two — so every dotted entry
in every policy was unreachable.

The cross-language decision contract is covered by conformance/vectors.json. These tests
cover the derivation rule itself, including the cases where it must refuse to derive.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _load(name: str):
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"pf_{name}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


actname = _load("actname")


class CanonicalFormTests(unittest.TestCase):
    def test_prefixed_mcp_act_becomes_dotted(self):
        self.assertEqual(
            actname.canonical_act("mcp__example_server__email_send_now"),
            "email.send_now",
        )

    def test_server_segment_may_contain_underscores(self):
        # The separator is the double underscore, so a server named `example_server` must
        # not be mistaken for server `example` plus domain `server`.
        self.assertEqual(
            actname.canonical_act("mcp__a_b_c_d__contacts_get_tags"),
            "contacts.get_tags",
        )

    def test_multi_word_action_keeps_all_of_its_words(self):
        self.assertEqual(
            actname.canonical_act(
                "mcp__example_server__contacts_get_segment_members_hashed"
            ),
            "contacts.get_segment_members_hashed",
        )

    def test_native_act_has_nothing_to_canonicalize(self):
        for act in ("terminal", "read_file", "browser_exec", "tool_describe"):
            self.assertIsNone(actname.canonical_act(act), act)

    def test_already_dotted_act_is_left_alone(self):
        self.assertIsNone(actname.canonical_act("email.send_now"))

    def test_derived_facet_is_left_alone(self):
        # Derived facets already carry a dot and must not be rewritten.
        self.assertIsNone(actname.canonical_act("terminal.network"))

    def test_prefix_with_no_action_segment_yields_nothing(self):
        # `email` alone has no action, so there is no domain.action form to build.
        self.assertIsNone(actname.canonical_act("mcp__example_server__email"))

    def test_empty_input_is_safe(self):
        self.assertIsNone(actname.canonical_act(""))


class AmbiguousSpellingTests(unittest.TestCase):
    def test_single_underscore_spelling_is_refused_not_guessed(self):
        # Could be server `example`/domain `server`, or server `example_server`/domain
        # `calendar`. Nothing in the string decides it. A wrong guess would map the act
        # onto a policy entry that may grant something else, so it must deny instead.
        act = "mcp_example_server_calendar_create_event"
        self.assertTrue(actname.is_ambiguous_mcp_name(act))
        self.assertIsNone(actname.canonical_act(act))

    def test_double_underscore_spelling_is_not_flagged_ambiguous(self):
        self.assertFalse(
            actname.is_ambiguous_mcp_name("mcp__example_server__email_send_now")
        )

    def test_a_native_act_starting_with_mcp_is_not_flagged(self):
        self.assertFalse(actname.is_ambiguous_mcp_name("terminal"))


class ResolutionOrderTests(unittest.TestCase):
    def test_emitted_name_is_tried_before_the_canonical_form(self):
        # The safety property the whole change rests on: an act that already resolves must
        # resolve to the same entry, so canonicalization can only reach what was unreachable.
        self.assertEqual(
            actname.resolution_candidates("mcp__example_server__email_send_now"),
            ["mcp__example_server__email_send_now", "email.send_now"],
        )

    def test_native_act_has_a_single_candidate(self):
        self.assertEqual(actname.resolution_candidates("terminal"), ["terminal"])

    def test_dotted_act_does_not_duplicate_itself(self):
        self.assertEqual(
            actname.resolution_candidates("email.send_now"), ["email.send_now"]
        )


if __name__ == "__main__":
    unittest.main()
