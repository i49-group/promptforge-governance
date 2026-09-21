"""Tests for the build identity this plugin reports.

The property that matters is not that the hash has any particular value — it is that the hash
**changes when the enforcing code changes**. A build identifier that stays constant across a code
change is worse than none, because a standing check would then confirm a stale plugin as current.
That is the failure this module was written to end (P-121), so it is asserted directly rather than
assumed from the fact that a hash is being computed.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import build as build_mod


class RecordedBuild(unittest.TestCase):
    """`conformance/pep-build.json` must match the build this code actually reports.

    PromptForge cannot compute this hash — the sources live only in this repository — so its
    enforcement-point check compares the fleet against the recorded value instead. That comparison
    is the only way a *uniformly* stale fleet is detectable: when every gateway is equally out of
    date there is no disagreement between them to observe, so the check that proves "the agents
    agree with each other" passes with the entire fleet behind (P-138).

    The whole scheme rests on the recorded value being current, and the only thing that can keep it
    current is a test that fails when it is not. Without this, editing `pdp.py` and forgetting to
    regenerate leaves PromptForge confidently comparing the fleet against a hash that describes code
    nobody is running — a check that reports health while measuring the wrong thing, which is the
    defect class this repository has spent P-121, P-128 and P-134 removing.
    """

    def setUp(self) -> None:
        self.path = Path(build_mod.__file__).resolve().parent.parent / "conformance" / "pep-build.json"
        self.recorded = json.loads(self.path.read_text())

    def test_the_recorded_build_matches_this_code(self) -> None:
        self.assertEqual(
            self.recorded["build"],
            build_mod.BUILD,
            f"\n\n{self.path.name} records {self.recorded['build']} but this code computes "
            f"{build_mod.BUILD}.\n\nAn enforcing module changed and the recorded build was not "
            f"regenerated. Until it is, PromptForge compares the fleet against a hash for code "
            f"nobody runs — so a stale fleet reads as current and a current fleet reads as stale.\n\n"
            f"Fix: pnpm build:record-pep-build\n",
        )

    def test_the_recorded_module_list_matches_what_is_hashed(self) -> None:
        """A module added to the hash but missing here would make the record silently incomplete."""
        self.assertEqual(list(self.recorded["modules"]), list(build_mod._RUNTIME_MODULES))


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

        There is deliberately no exclusion list. This test, `test_import_modes._SIBLINGS` and the
        dual-mode import rule together assert one property of this directory — **everything in it is
        enforcing code a host loads** — and the way to keep that property is to keep the tests
        absolute and put non-runtime tooling somewhere else. All three flagged `record_build.py` on
        the day it was written; it moved to `scripts/` rather than earning three exemptions, because
        each exemption would have made its guard slightly worse at catching the next enforcing module
        added without care.
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


class RuntimeRole(unittest.TestCase):
    """What kind of process is enforcing, so residency can be judged (P-134).

    The duplicate-enforcement-point check counts resident instances per agent and fails on two. That
    is right for gateways and wrong the moment a human opens a CLI session beside one — which is how
    this was found: a bare `hermes` on a tty was reported as a breach when it was a person working.
    """

    def _role(self, argv: list) -> str:
        with mock.patch.object(sys, "argv", argv):
            return build_mod.runtime_role()

    def test_a_gateway_is_a_gateway(self) -> None:
        self.assertEqual(
            self._role(["hermes", "--profile", "alex", "gateway", "run"]), build_mod.ROLE_GATEWAY
        )

    def test_a_bare_session_is_interactive(self) -> None:
        self.assertEqual(self._role(["hermes"]), build_mod.ROLE_INTERACTIVE)

    def test_a_session_with_only_flags_is_interactive(self) -> None:
        self.assertEqual(
            self._role(["hermes", "--profile", "alex"]), build_mod.ROLE_INTERACTIVE
        )

    def test_the_dashboard_is_the_dashboard(self) -> None:
        self.assertEqual(self._role(["hermes", "dashboard"]), build_mod.ROLE_DASHBOARD)

    def test_a_profile_named_like_a_utility_is_still_a_gateway(self) -> None:
        # A flag's *value* is not the command. Getting this wrong would let a real gateway be
        # excused from the one-per-agent rule by the name of the profile it runs.
        self.assertEqual(
            self._role(["hermes", "--profile", "dashboard", "gateway", "run"]),
            build_mod.ROLE_GATEWAY,
        )

    def test_an_inline_flag_value_is_not_the_command(self) -> None:
        self.assertEqual(
            self._role(["hermes", "--profile=dashboard", "gateway", "run"]),
            build_mod.ROLE_GATEWAY,
        )

    def test_an_unrecognised_command_is_not_promoted_to_gateway(self) -> None:
        """Fails toward being counted, not excused. `gateway` is the role required to be alone, so
        an unknown shape must never land there — that would launder a second gateway into silence.
        """
        self.assertEqual(
            self._role(["hermes", "some-future-serve-command"]), build_mod.ROLE_COMMAND
        )

    def test_the_role_is_carried_on_every_fetch(self) -> None:
        with mock.patch.object(sys, "argv", ["hermes"]):
            self.assertEqual(
                build_mod.build_headers()["X-PromptForge-Pep-Role"], build_mod.ROLE_INTERACTIVE
            )

    def test_the_role_is_never_absent(self) -> None:
        # An absent role is indistinguishable from a pre-1.5 plugin, which the check must warn
        # about. This build has no excuse to produce that state.
        for argv in ([], ["hermes"], ["hermes", "gateway"], ["hermes", "-x"], ["hermes", "--"]):
            with mock.patch.object(sys, "argv", argv):
                self.assertTrue(build_mod.build_headers()["X-PromptForge-Pep-Role"])


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

    def _register_with_argv(self, argv: list) -> tuple:
        """Returns (heartbeat_started, hooks_registered) for a given command line."""
        started: list = []
        hooks: dict = {}

        class Ctx:
            def register_hook(self, name: str, fn: object) -> None:
                hooks[name] = fn

        with mock.patch.object(sys, "argv", argv):
            with mock.patch.object(self.plugin, "_start_refresh_loop", lambda: started.append(True)):
                self.plugin.register(Ctx())
        return bool(started), set(hooks)

    def test_the_dashboard_does_not_heartbeat_but_is_still_governed(self) -> None:
        """It shares the default profile's home, so it loaded the plugin and counted as a second
        resident enforcement point for that agent (P-128). Hooks must survive regardless: a process
        we judged non-agent that turns out to execute tools has to stay governed."""
        beats, hooks = self._register_with_argv(["hermes", "dashboard"])
        self.assertFalse(beats)
        self.assertIn("pre_tool_call", hooks)

    def test_a_gateway_heartbeats(self) -> None:
        beats, _ = self._register_with_argv(["hermes", "--profile", "alex", "gateway", "run"])
        self.assertTrue(beats)

    def test_a_bare_interactive_session_heartbeats(self) -> None:
        """The P-128 process was exactly this — long-lived, executes tools, must stay visible."""
        beats, _ = self._register_with_argv(["hermes"])
        self.assertTrue(beats)

    def test_a_profile_named_like_a_utility_still_heartbeats(self) -> None:
        # A flag's value must not be mistaken for the subcommand and silence a real gateway.
        beats, _ = self._register_with_argv(["hermes", "--profile", "dashboard", "gateway", "run"])
        self.assertTrue(beats)

    def test_an_unrecognised_command_heartbeats(self) -> None:
        """Fails toward visibility: a spurious heartbeat is noise, a missing one is a blind spot."""
        beats, _ = self._register_with_argv(["hermes", "some-future-serve-command"])
        self.assertTrue(beats)

    def test_a_failing_first_refresh_does_not_escape(self) -> None:
        # A network error at load must not take the gateway down with it.
        def explode() -> object:
            raise RuntimeError("promptforge unreachable")

        with mock.patch.object(self.plugin, "_get_pdp", explode):
            with self.assertLogs(self.plugin.logger, level="WARNING"):
                self.plugin._refresh_loop(interval_s=999)


if __name__ == "__main__":
    unittest.main()
