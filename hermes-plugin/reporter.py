"""
Fire-and-forget reporting of local PDP decisions back to PromptForge.

The receiving endpoint `POST /api/governance/decisions` existed before this module
did, with no caller — so no decision was ever recorded, and we could not tell "no
denials happened" from "the emitter never fires". This is the emitter.

Two constraints shape the design:

  - **It must never delay or fail a tool call.** Reporting is enqueued and posted
    from a daemon thread. A full queue drops rather than blocks, and every error
    is swallowed after logging. Governance reporting that can wedge the agent it
    reports on is worse than no reporting.
  - **It must not run unless PromptForge asked for it.** These rows carry act
    names off the host, so the emitter stays silent until the agent's published
    policy sets `report_decisions`.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import urllib.error
import urllib.request
from typing import Any, Optional

logger = logging.getLogger("promptforge.governance.reporter")

DECISIONS_PATH = "/api/governance/decisions"

# Deep enough to absorb a burst of tool calls, shallow enough that a PromptForge
# outage cannot turn into unbounded memory on the host.
QUEUE_MAX = 256

# Generous relative to the 2 s evaluate timeout: nothing waits on this, so a slow
# write costs patience on a background thread rather than latency on an act.
POST_TIMEOUT_S = 5.0


class DecisionReporter:
    """Bounded queue plus one daemon worker. Enqueue never blocks and never raises."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        environment: str = "production",
        timeout_s: float = POST_TIMEOUT_S,
        queue_max: int = QUEUE_MAX,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.environment = environment
        self.timeout_s = timeout_s
        self._queue: queue.Queue = queue.Queue(maxsize=queue_max)
        self._worker: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # Counters, not just logs: a reporter that silently stops reporting is the
        # exact failure this whole exercise exists to make visible.
        self.enqueued = 0
        self.dropped = 0
        self.sent = 0
        self.failed = 0
        self.rejected = 0
        self._warn_counts: dict = {}

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run,
                name="promptforge-decision-reporter",
                daemon=True,
            )
            self._worker.start()

    def report(
        self,
        *,
        agent_key: str,
        tool_name: str,
        decision: str,
        reasons: Optional[list] = None,
        correlation_id: Optional[str] = None,
        policy_version: Optional[str] = None,
    ) -> bool:
        """Enqueue one decision. Returns False if it was dropped."""
        payload = {
            "agent_key": agent_key,
            "tool_name": tool_name,
            "decision": decision,
            "reasons": list(reasons or []),
            "correlation_id": correlation_id,
            "policy_version": policy_version,
            "environment": self.environment,
        }
        try:
            self._ensure_worker()
            self._queue.put_nowait(payload)
            self.enqueued += 1
            return True
        except queue.Full:
            self.dropped += 1
            self._warn_occasionally(
                "dropped",
                "PromptForge decision reports dropping, queue full (dropped=%d)",
                self.dropped,
            )
            return False
        except Exception as exc:  # noqa: BLE001 — reporting never breaks the caller
            self.dropped += 1
            self._warn_occasionally(
                "enqueue", "PromptForge decision report not enqueued: %s", exc
            )
            return False

    def _warn_occasionally(self, key: str, msg: str, *args: Any) -> None:
        """Warn on the first failure of a kind, then every hundredth.

        These paths logged at debug until 09-16-2026, and the gateway logs at info, so a
        reporter that failed on every call produced a log identical to one that never had
        anything to report. The whole point of this module is to make decisions visible, so
        its own failure is the last thing that should be quiet. Rate-limited because a broken
        endpoint fails once per tool call and a warning per call would bury everything else.
        """
        n = self._warn_counts.get(key, 0) + 1
        self._warn_counts[key] = n
        if n == 1 or n % 100 == 0:
            logger.warning(msg + " [occurrence %d]", *args, n)

    def _run(self) -> None:
        while True:
            payload = self._queue.get()
            try:
                self._post(payload)
            except Exception as exc:  # noqa: BLE001
                self.failed += 1
                self._warn_occasionally(
                    "post", "PromptForge decision report failed: %s", exc
                )
            finally:
                self._queue.task_done()

    def _post(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{DECISIONS_PATH}",
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        # The endpoint is fail-soft by contract: it answers 200 with
        # accepted=false when the org has not opted in or the write failed. Count
        # that separately from a transport failure so "reporting is on but nothing
        # is landing" is distinguishable from "the host cannot reach PromptForge".
        accepted = True
        try:
            parsed: Any = json.loads(raw)
            accepted = bool((parsed.get("data") or {}).get("accepted", True))
        except Exception:  # noqa: BLE001
            pass

        if accepted:
            self.sent += 1
        else:
            self.rejected += 1
            # accepted=false means the decision did not reach the record: either the agent has
            # not opted in, or the write failed. Note that a sampled-away allow is still
            # accepted=true, so this is never merely sampling.
            self._warn_occasionally(
                "rejected",
                "PromptForge did not record a decision for %s (opt-in off, or write failed)",
                payload.get("tool_name"),
            )

    def drain(self, timeout_s: float = 2.0) -> bool:
        """Wait for the queue to empty. For tests and shutdown, not the hot path."""
        idle = threading.Event()
        step = 0.01
        waited = 0.0
        while waited < timeout_s:
            if self._queue.unfinished_tasks == 0:
                return True
            idle.wait(step)
            waited += step
        return self._queue.unfinished_tasks == 0

    def stats(self) -> dict:
        return {
            "enqueued": self.enqueued,
            "dropped": self.dropped,
            "sent": self.sent,
            "failed": self.failed,
            "rejected": self.rejected,
        }
