"""Tests for the build identity this plugin reports.

The property that matters is not that the hash has any particular value — it is that the hash
**changes when the enforcing code changes**. A build identifier that stays constant across a code
change is worse than none, because a standing check would then confirm a stale plugin as current.
That is the failure this module was written to end (P-121), so it is asserted directly rather than
assumed from the fact that a hash is being computed.
"""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from unittest import mock

import build as build_mod


class BuildIdentity(unittest.TestCase):
    def test_the_build_is_a_short_stable_hash(self) -> None:
        self.assertRegex(build_mod.BUILD, r"^[0-9a-f]{12}$")
        self.assertEqual(build_mod.BUILD, build_mod._compute_build())

    def test_editing_an_enforcing_module_changes_the_build(self) -> None:
        """The whole point. Proven by hashing with one module's bytes altered."""
        here = Path(build_mod.__file__).resolve().parent
        real = build_mod._compute_build()

        original_read = Path.read_bytes

        def tampered(self: Path) -> bytes:
            data = original_read(self)
            return data + b"\n# a change to enforcing logic\n" if self.name == "pdp.py" else data

        with mock.patch.object(Path, "read_bytes", tampered):
            changed = build_mod._compute_build()

        self.assertNotEqual(real, changed)
        self.assertTrue((here / "pdp.py").exists())

    def test_every_runtime_module_is_covered(self) -> None:
        """A module absent from the list is a module whose changes are invisible.

        `derive.py` and `reporter.py` were both added after the first version of this plugin, and
        either could have been left out of the hash without any test noticing.
        """
        here = Path(build_mod.__file__).resolve().parent
        on_disk = {
            p.name
            for p in here.glob("*.py")
            if not p.name.startswith("test_") and p.name != "conftest.py"
        }
        self.assertEqual(on_disk, set(build_mod._RUNTIME_MODULES))

    def test_tests_do_not_affect_the_build(self) -> None:
        # A test-only change cannot alter a decision, so it must not report a new build and
        # trigger a staleness alert for every agent.
        self.assertNotIn("test_build.py", build_mod._RUNTIME_MODULES)
        for name in build_mod._RUNTIME_MODULES:
            self.assertFalse(name.startswith("test_"))

    def test_a_missing_source_file_degrades_rather_than_raising(self) -> None:
        # Enforcement must survive an unidentifiable build; the standing check treats "unknown"
        # as a finding, so this reports the problem instead of causing an outage.
        def explode(self: Path) -> bytes:
            raise OSError("gone")

        with mock.patch.object(Path, "read_bytes", explode):
            self.assertEqual(build_mod._compute_build(), "unknown")

    def test_the_headers_carry_build_version_and_instance(self) -> None:
        headers = build_mod.build_headers()
        self.assertEqual(headers["X-PromptForge-Pep-Build"], build_mod.BUILD)
        self.assertEqual(headers["X-PromptForge-Pep-Version"], build_mod.VERSION)
        self.assertEqual(headers["X-PromptForge-Pep-Instance"], build_mod.INSTANCE)


class InstanceIdentity(unittest.TestCase):
    """The instance id is what makes a second enforcement point visible.

    Two processes on the same build send the same BUILD, so the unsupervised gateway that held
    zander's identity for six days (P-128) was only detectable because it was *stale*. One running
    current code would have been invisible. The id must therefore be per-process, and must not be
    derived from anything the two processes share.
    """

    def test_the_instance_is_a_short_hex_id(self) -> None:
        self.assertRegex(build_mod.INSTANCE, r"^[0-9a-f]{12}$")

    def test_the_instance_is_not_the_build(self) -> None:
        self.assertNotEqual(build_mod.INSTANCE, build_mod.BUILD)

    def test_a_second_process_gets_a_different_instance(self) -> None:
        """Proven across real interpreters — two imports in one process would share the module."""
        import subprocess  # noqa: PLC0415
        import sys  # noqa: PLC0415

        here = Path(build_mod.__file__).resolve().parent
        ids = {
            subprocess.run(
                [sys.executable, "-c", "import build; print(build.INSTANCE)"],
                cwd=here,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            for _ in range(2)
        }
        self.assertEqual(len(ids), 2, "each process must be distinguishable")

    def test_the_instance_is_stable_within_a_process(self) -> None:
        # Otherwise every fetch would look like a new enforcement point.
        self.assertEqual(
            build_mod.build_headers()["X-PromptForge-Pep-Instance"],
            build_mod.build_headers()["X-PromptForge-Pep-Instance"],
        )


class FetchCarriesTheBuild(unittest.TestCase):
    def test_the_pdp_sends_the_build_header_on_a_fetch(self) -> None:
        """Asserted through the real fetch path: a header computed but never sent is inert."""
        import pdp as pdp_mod

        client = pdp_mod.GovernancePdp(
            base_url="https://example.test",
            token="t",
            agent_key="a",
            environment="production",
            verify_key="k",
        )

        captured: dict = {}

        class FakeResponse:
            headers = {"ETag": "e"}

            def read(self) -> bytes:
                return b'{"success":true,"data":{}}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

        def fake_urlopen(req, timeout=None):  # noqa: ANN001
            captured.update(req.headers)
            return FakeResponse()

        with mock.patch.object(pdp_mod.urllib.request, "urlopen", fake_urlopen):
            client._fetch_json("/api/governance/bundles/a")

        # urllib title-cases header keys.
        self.assertEqual(captured.get("X-promptforge-pep-build"), build_mod.BUILD)
        self.assertEqual(captured.get("X-promptforge-pep-version"), build_mod.VERSION)


class HeartbeatIsIndependentOfSessions(unittest.TestCase):
    """The heartbeat has to run whether or not anyone talks to the agent.

    It originally started inside `on_session_start`, so an idle gateway never contacted PromptForge
    at all — making "governed and quiet" and "not governed" the same observation from our side,
    which is the exact confusion P-121 exists to remove. It also meant a gateway holding broken
    credentials looked healthy until someone happened to send it a message.
    """

    def setUp(self) -> None:
        import __init__ as plugin  # noqa: PLC0415

        self.plugin = plugin
        plugin._refresh_stop.set()  # keep any thread we start from looping
        self.addCleanup(plugin._refresh_stop.clear)

    def test_registering_starts_the_heartbeat(self) -> None:
        started: list[bool] = []
        with mock.patch.object(self.plugin, "_start_refresh_loop", lambda: started.append(True)):
            hooks: dict = {}

            class Ctx:
                def register_hook(self, name: str, fn: object) -> None:
                    hooks[name] = fn

            self.plugin.register(Ctx())

        self.assertEqual(started, [True], "register() must start the heartbeat")
        self.assertIn("pre_tool_call", hooks)

    def test_the_loop_refreshes_before_it_waits(self) -> None:
        """A restarted gateway must not look dead for a whole interval before its first fetch."""
        calls: list[str] = []

        class FakePdp:
            def refresh(self) -> dict:
                calls.append("refresh")
                return {}

        with mock.patch.object(self.plugin, "_get_pdp", lambda: FakePdp()):
            with mock.patch.object(self.plugin, "_mark_ready", lambda meta: None):
                # _refresh_stop is set, so this returns after exactly one pass.
                self.plugin._refresh_loop(interval_s=999)

        self.assertEqual(calls, ["refresh"])

    def test_a_failing_first_refresh_does_not_escape(self) -> None:
        # A network error at load must not take the gateway down with it.
        def explode() -> object:
            raise RuntimeError("promptforge unreachable")

        with mock.patch.object(self.plugin, "_get_pdp", explode):
            with self.assertLogs(self.plugin.logger, level="WARNING"):
                self.plugin._refresh_loop(interval_s=999)


if __name__ == "__main__":
    unittest.main()
