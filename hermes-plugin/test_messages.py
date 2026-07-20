"""Tests for user-facing block copy."""

from __future__ import annotations

import unittest

import messages as msg


class MessageTests(unittest.TestCase):
    def test_setup_not_found_has_admin_steps(self) -> None:
        text = msg.block_setup(
            tool_name="calendar.get_events",
            agent_key="penn",
            exc="HTTP 404: not found",
        )
        self.assertIn("TOOL BLOCKED BY PROMPTFORGE", text)
        self.assertIn("What to do:", text)
        self.assertIn(msg.ADMIN_URL, text)
        self.assertIn("Published", text)

    def test_setup_auth(self) -> None:
        text = msg.block_setup(
            tool_name="x",
            agent_key="leo",
            exc="HTTP 401: Unauthorized",
        )
        self.assertIn("credentials", text.lower())
        self.assertIn("Integrations", text)

    def test_unknown_tool_policy(self) -> None:
        text = msg.block_policy(
            tool_name="email.send",
            agent_key="penn",
            decision="deny",
            reasons=["unknown_tool", "tool:email.send"],
            bundle_version="v1",
            pdp_state="normal",
        )
        self.assertIn("not in the published Act inventory", text)
        self.assertIn("Add tool", text)
        self.assertIn("Tell the user clearly", text)

    def test_session_not_ready(self) -> None:
        text = msg.session_not_ready_context(
            agent_key="penn", error="HTTP 404"
        )
        self.assertIn("NOT READY", text)
        self.assertIn("tools will not run", text)


if __name__ == "__main__":
    unittest.main()
