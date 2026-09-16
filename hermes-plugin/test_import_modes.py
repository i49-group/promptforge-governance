"""
The plugin must import under BOTH loading modes hosts actually use.

This exists because of a real deployment failure. A module here used a bare absolute import
of a sibling (`from derive import ...`). Every test passed, because the suite runs with this
directory on `sys.path` — the flat mode. The host loads the directory as a *package*, where
that import does not resolve, and `__init__`'s own ImportError fallback then re-raised as
`No module named 'pdp'`, naming the wrong module entirely. The host logged one WARNING line,
carried on without the plugin, and the agent ran ungoverned until someone read the log.

Two lessons are encoded here:

  1. A test suite that only exercises one import mode cannot see a break in the other. So
     both are asserted, from the outside, the way a host does it.
  2. Failing to load is the worst possible failure for a policy enforcement point — it does
     not deny, it disappears. It deserves a test of its own rather than being noticed after
     the fact in a log.

Add a sibling module and it must appear in `_SIBLINGS` below; the guard test will otherwise
fail and tell you why.
"""

from __future__ import annotations

import importlib
import importlib.util
import pathlib
import re
import subprocess
import sys
import textwrap
import unittest

HERE = pathlib.Path(__file__).resolve().parent
_SIBLINGS = ("pdp", "derive", "messages", "reporter")


class PackageMode(unittest.TestCase):
    """Load the directory as a package, as a host does, in a clean interpreter."""

    def test_plugin_imports_as_a_package(self) -> None:
        # A subprocess, deliberately: this process already has the siblings imported flat,
        # which is exactly the condition that hides the bug.
        script = textwrap.dedent(
            f"""
            import importlib, importlib.util, sys, pathlib
            root = pathlib.Path({str(HERE)!r})
            sys.path.insert(0, str(root.parent))
            spec = importlib.util.spec_from_file_location(
                "pf_plugin_pkg", root / "__init__.py",
                submodule_search_locations=[str(root)],
            )
            mod = importlib.util.module_from_spec(spec)
            sys.modules["pf_plugin_pkg"] = mod
            spec.loader.exec_module(mod)
            assert hasattr(mod, "pre_tool_call"), "hook missing after package load"
            assert hasattr(mod, "post_tool_call"), "post hook missing after package load"
            print("PACKAGE_OK")
            """
        )
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True
        )
        self.assertIn(
            "PACKAGE_OK",
            proc.stdout,
            f"package-mode import failed:\n{proc.stderr}",
        )

    def test_each_sibling_imports_as_a_submodule(self) -> None:
        for name in _SIBLINGS:
            script = textwrap.dedent(
                f"""
                import importlib, importlib.util, sys, pathlib
                root = pathlib.Path({str(HERE)!r})
                spec = importlib.util.spec_from_file_location(
                    "pf_pkg_{name}", root / "__init__.py",
                    submodule_search_locations=[str(root)],
                )
                mod = importlib.util.module_from_spec(spec)
                sys.modules["pf_pkg_{name}"] = mod
                spec.loader.exec_module(mod)
                importlib.import_module("pf_pkg_{name}.{name}")
                print("OK")
                """
            )
            proc = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True
            )
            self.assertIn("OK", proc.stdout, f"{name} failed as submodule:\n{proc.stderr}")


class FlatMode(unittest.TestCase):
    """The mode the rest of the suite uses, asserted rather than assumed."""

    def test_siblings_import_flat(self) -> None:
        for name in _SIBLINGS:
            self.assertTrue(
                importlib.import_module(name), f"{name} failed to import flat"
            )


class ImportStyleGuard(unittest.TestCase):
    """
    Catch the defect by shape as well as by behaviour, so a new module is caught before it
    is ever loaded on a host.
    """

    def test_no_bare_absolute_sibling_imports(self) -> None:
        pattern = re.compile(
            r"^[ \t]*(?:from|import)[ \t]+(" + "|".join(_SIBLINGS) + r")\b"
        )
        for path in sorted(HERE.glob("*.py")):
            if path.name.startswith("test_"):
                continue  # tests run flat by design
            for lineno, line in enumerate(
                path.read_text().splitlines(), start=1
            ):
                if not pattern.match(line):
                    continue
                # Legitimate only inside an ImportError fallback, paired with a relative
                # import above it. Require the marker rather than parsing the block.
                self.assertIn(
                    "type: ignore",
                    line,
                    f"{path.name}:{lineno} imports a sibling absolutely outside a "
                    f"fallback branch — this breaks package-mode loading:\n  {line.strip()}",
                )

    def test_every_sibling_is_covered_by_this_file(self) -> None:
        found = {
            p.stem
            for p in HERE.glob("*.py")
            if not p.name.startswith("test_") and p.stem != "__init__"
        }
        self.assertEqual(
            found,
            set(_SIBLINGS),
            "a module was added or removed without updating _SIBLINGS, so it is not "
            "checked for package-mode import safety",
        )


if __name__ == "__main__":
    unittest.main()
