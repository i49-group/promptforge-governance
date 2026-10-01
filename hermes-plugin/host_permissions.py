"""
Report the permissions the host keeps outside the signed policy.

Hermes holds standing grants of its own: "always" approvals in a chat add entries to the
profile's `command_allowlist`, and `approvals.*` decides whether dangerous commands are asked
about at all. None of that is in the Act policy, so PromptForge could not see it, and an
approval clicked once in a chat silently widened what the agent may run for good. This module
sends PromptForge a read-only snapshot so those grants show up next to the policy.

Same constraints as `reporter.py`:

  - **It must never delay or fail a tool call.** The snapshot is read and posted from the
    refresh thread and a daemon worker; every error is logged and swallowed.
  - **It must not carry secrets off the host.** An exact-command approval is already a hash.
    Any other entry Hermes's own redaction would alter, or that looks like a token, is sent as
    a hash of itself, never in clear. PromptForge applies the same rule again on arrival.

Only the gateway reports: it is the long-lived process that answers chats, and so the one
whose grants matter. A one-shot command reading the same profile would only repeat it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger("promptforge.governance.host_permissions")

HOST_PERMISSIONS_PATH = "/api/governance/host-permissions"
SCHEMA_VERSION = 1
HARNESS = "hermes"

# PromptForge expects a report within a day; resending a quarter as often keeps a quiet but
# healthy agent from looking stale after one missed post.
RESEND_SECONDS = 6 * 60 * 60

POST_TIMEOUT_S = 5.0

# Matches PromptForge's own limits, so a report is never rejected for size alone.
MAX_APPROVALS = 500
MAX_DENY_RULES = 200
MAX_KEY_LENGTH = 512
MAX_PLAIN_KEY_LENGTH = 160

UNATTENDED_MODE_KEYS = ("cron_mode", "single_query_mode", "unattended_mode")

_SECRET_SHAPES = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|github_pat_|xox[abposr]-|AKIA[0-9A-Z]{8,}"
    r"|bearer\s+\S|eyJ[A-Za-z0-9_-]{8,}\.|[A-Za-z0-9+_=-]{32,})",
    re.IGNORECASE,
)


def _hash_key(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _default_redact(text: str) -> str:
    try:
        from agent.redact import redact_sensitive_text  # type: ignore

        return redact_sensitive_text(text, force=True)
    except Exception:  # noqa: BLE001 — not running inside Hermes, or an older Hermes
        return text


def classify(entry: str) -> str:
    if entry.startswith("=command:"):
        return "command_hash"
    if entry.startswith("plugin_rule:"):
        return "rule"
    if entry.startswith("/"):
        return "binary"
    return "pattern"


def scrub(entry: str, kind: str, redact: Callable[[str], str] = _default_redact) -> str:
    """The key as it may leave the host: in clear only if nothing about it looks secret."""
    if kind == "command_hash":
        return entry[:MAX_KEY_LENGTH]
    if (
        len(entry) > MAX_PLAIN_KEY_LENGTH
        or _SECRET_SHAPES.search(entry)
        or redact(entry) != entry
    ):
        return _hash_key(entry)
    return entry


def build_snapshot(config: dict, redact: Callable[[str], str] = _default_redact) -> dict:
    """The report body, minus identity. Pure, so it is testable without Hermes."""
    approvals = config.get("approvals") if isinstance(config.get("approvals"), dict) else {}
    allowlist = config.get("command_allowlist") or []
    if not isinstance(allowlist, list):
        allowlist = []

    seen: set = set()
    entries = []
    for raw in allowlist:
        if not isinstance(raw, str) or not raw.strip():
            continue
        text = raw.strip()
        kind = classify(text)
        key = scrub(text, kind, redact)
        if (key, kind) in seen:
            continue
        seen.add((key, kind))
        entries.append({"key": key, "kind": kind})
    entries.sort(key=lambda e: (e["kind"], e["key"]))

    deny = approvals.get("deny") or []
    deny_rules = sorted(
        {scrub(d.strip(), "pattern", redact) for d in deny if isinstance(d, str) and d.strip()}
    )

    mode = approvals.get("mode")
    unattended = {
        k: str(approvals[k]) for k in UNATTENDED_MODE_KEYS if approvals.get(k) is not None
    }
    return {
        "approval_mode": str(mode) if mode is not None else None,
        "unattended_modes": unattended,
        "permanent_approvals": entries[:MAX_APPROVALS],
        "deny_rules": deny_rules[:MAX_DENY_RULES],
    }


def digest(snapshot: dict) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_host_config() -> dict:
    """The merged profile config Hermes itself uses, or the raw file when Hermes is not importable."""
    try:
        from hermes_cli.config import load_config  # type: ignore

        cfg = load_config()
        return cfg if isinstance(cfg, dict) else {}
    except Exception:  # noqa: BLE001
        pass
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    try:
        import yaml  # type: ignore

        with open(os.path.join(home, "config.yaml"), encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("PromptForge host-permissions: could not read host config: %s", exc)
        return {}


class HostPermissionsReporter:
    """Sends a snapshot when it changes, and at least every RESEND_SECONDS. Never blocks the caller."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        agent_key: str,
        environment: str = "production",
        load_config: Callable[[], dict] = load_host_config,
        redact: Callable[[str], str] = _default_redact,
        clock: Callable[[], float] = time.time,
        timeout_s: float = POST_TIMEOUT_S,
        resend_s: float = RESEND_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.agent_key = agent_key
        self.environment = environment
        self._load_config = load_config
        self._redact = redact
        self._clock = clock
        self.timeout_s = timeout_s
        self.resend_s = resend_s
        # One slot: only the newest snapshot is worth sending.
        self._queue: queue.Queue = queue.Queue(maxsize=1)
        self._worker: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_digest: Optional[str] = None
        self._last_sent_at: float = 0.0
        self.sent = 0
        self.failed = 0
        self.rejected = 0
        self.skipped = 0

    def check(self) -> bool:
        """Read the config and enqueue a report if one is due. Returns whether one was enqueued."""
        try:
            snapshot = build_snapshot(self._load_config(), self._redact)
            snap_digest = digest(snapshot)
            now = self._clock()
            with self._lock:
                due = snap_digest != self._last_digest or now - self._last_sent_at >= self.resend_s
            if not due:
                self.skipped += 1
                return False
            payload = {
                "schema": SCHEMA_VERSION,
                "agent_key": self.agent_key,
                "environment": self.environment,
                "harness": HARNESS,
                "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                **snapshot,
            }
            self._ensure_worker()
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                pass
            self._queue.put_nowait((snap_digest, payload))
            return True
        except Exception as exc:  # noqa: BLE001 — reporting never breaks the caller
            logger.warning("PromptForge host-permissions report not prepared: %s", exc)
            return False

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run, name="promptforge-host-permissions", daemon=True
            )
            self._worker.start()

    def _run(self) -> None:
        while True:
            snap_digest, payload = self._queue.get()
            try:
                if self._post(payload):
                    with self._lock:
                        self._last_digest = snap_digest
                        self._last_sent_at = self._clock()
            except Exception as exc:  # noqa: BLE001
                self.failed += 1
                logger.warning("PromptForge host-permissions report failed: %s", exc)
            finally:
                self._queue.task_done()

    def _post(self, payload: dict) -> bool:
        req = urllib.request.Request(
            f"{self.base_url}{HOST_PERMISSIONS_PATH}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        accepted = True
        try:
            accepted = bool((json.loads(raw).get("data") or {}).get("accepted", True))
        except Exception:  # noqa: BLE001
            pass
        if accepted:
            self.sent += 1
            return True
        # Not marked sent, so the next refresh tries again rather than waiting six hours.
        self.rejected += 1
        logger.warning("PromptForge did not record the host-permissions report (write failed)")
        return False

    def drain(self, timeout_s: float = 2.0) -> bool:
        """Wait for the queue to empty. For tests and shutdown, not the hot path."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0
