"""The PEP's secrets must not reach the tools it governs.

Hermes hands `terminal` and `execute_code` a scrubbed copy of the process environment, but its
scrub list does not name the PEP's keys. Anything left in `os.environ` is therefore one `env`
away from the model, through a command that names no secret and so derives no facet.
"""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock


def _load_gate():
    path = Path(__file__).resolve().parent / "__init__.py"
    spec = importlib.util.spec_from_file_location("pf_gate_secret_env", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()

ENV = {
    "PF_BASE_URL": "https://example.test",
    "PF_SERVICE_TOKEN": "pf_svc_example",
    "PF_BUNDLE_VERIFY_KEY": "verify-example",
    "PF_AGENT_KEY": "example_agent",
    "PF_REFRESH_SECONDS": "0",
}


class FakeCtx:
    def register_hook(self, name, fn):  # noqa: ARG002
        pass


class SecretEnvTests(unittest.TestCase):
    def setUp(self):
        gate._pdp = None
        self._env = mock.patch.dict(os.environ, ENV, clear=False)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        gate._pdp = None

    def test_register_captures_then_removes_secrets(self):
        gate.register(FakeCtx())
        self.assertNotIn("PF_SERVICE_TOKEN", os.environ)
        self.assertNotIn("PF_BUNDLE_VERIFY_KEY", os.environ)
        self.assertEqual(gate._pdp.token, "pf_svc_example")
        self.assertEqual(gate._pdp.verify_key, "verify-example")

    def test_non_secret_settings_stay(self):
        # The agent key and base URL identify the PEP; they are not credentials, and other
        # code (messages, status lines) still reads them from the environment.
        gate.register(FakeCtx())
        self.assertEqual(os.environ["PF_AGENT_KEY"], "example_agent")
        self.assertEqual(os.environ["PF_BASE_URL"], "https://example.test")

    def test_tool_call_removes_secrets_put_back_by_a_reload(self):
        # Hermes re-reads the profile .env several times per process. Whatever it restores,
        # the tool must still start without it.
        gate.register(FakeCtx())
        os.environ["PF_SERVICE_TOKEN"] = "pf_svc_example"
        os.environ["PF_BUNDLE_VERIFY_KEY"] = "verify-example"
        with mock.patch.object(gate._pdp, "refresh", side_effect=RuntimeError("offline")):
            gate.pre_tool_call(tool_name="terminal", args={"command": "env"}, task_id="t1")
        self.assertNotIn("PF_SERVICE_TOKEN", os.environ)
        self.assertNotIn("PF_BUNDLE_VERIFY_KEY", os.environ)

    def test_missing_credentials_leave_environment_alone_and_fail_closed(self):
        # Nothing was captured, so nothing is removed, and the call is still refused.
        os.environ.pop("PF_SERVICE_TOKEN")
        gate.register(FakeCtx())
        self.assertIsNone(gate._pdp)
        self.assertIn("PF_BUNDLE_VERIFY_KEY", os.environ)
        result = gate.pre_tool_call(tool_name="terminal", args={"command": "ls"}, task_id="t2")
        self.assertEqual(result["action"], "block")


if __name__ == "__main__":
    unittest.main()
