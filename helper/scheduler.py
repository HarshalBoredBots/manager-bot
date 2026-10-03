"""
manager/helper/scheduler.py
══════════════════════════════════════════════════════════════════════════════
Fair-queue scheduler for the Manager bot.

Responsibilities
────────────────
  1. Pull QUEUED jobs from MongoDB in fair order.
  2. Select an available Worker (lowest active_jobs wins).
  3. Atomically assign the job (QUEUED → ASSIGNED) and send a TASK message
     to the Worker Control Group via the shared String Session.
  4. Recover expired leases (ASSIGNED/ACKED → QUEUED) so failed workers
     never permanently lose a job.
  5. Mark workers OFFLINE when heartbeats are missing.

Fair scheduling algorithm
─────────────────────────
  Jobs are fetched sorted by (user_id ASC, created_at ASC) from MongoDB.
  We then interleave them round-robin by user_id before dispatching — so
  User A's 100 files never starve User B's 5 files.

  Example (concurrency = 4, 3 slots free):
    DB returns:  A1 A2 A3 B1 C1
    Round-robin: A1 B1 C1 A2   (user A gets 1 slot, B gets 1, C gets 1)

  Worker selection: among all available workers, pick the one with the
  fewest active_jobs (weighted fair for workers too).

Design constraints
──────────────────
  • String Session is the ONLY channel for Manager → Worker TASK messages.
  • Workers are identified by worker_id in every message — no per-worker sessions.
  • One String Session can receive all worker messages because every message
    carries worker_id / job_id / batch_id for routing.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING

from shared.protocol import (
    make_task, WORKER_OFFLINE, WORKER_ONLINE, WORKER_BUSY,
)

if TYPE_CHECKING:
    from helper.database import ManagerDB

logger = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────────────────
_SCHEDULER_INTERVAL   = 2.0    # seconds between scheduling passes
_LEASE_CHECK_INTERVAL = 30.0   # seconds between lease recovery passes
_OFFLINE_CHECK_INTERVAL = 60.0 # seconds between heartbeat staleness checks


class FairScheduler:
    """
    Pulls queued jobs from MongoDB and dispatches them to available workers
    via the Worker Control Group through the shared String Session client.
    """

    def __init__(self, db: "ManagerDB", control_group_id: int, session_client):
        """
        Parameters
        ──────────
        db                : ManagerDB instance
        control_group_id  : Telegram chat ID of the Worker Control Group
        session_client    : Running Pyrogram Client (String Session) for sending
        """
        self._db          = db
        self._group_id    = control_group_id
        self._session     = session_client

        self._running     = False
        self._tasks: list[asyncio.Task] = []

    # ──────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start all background scheduler loops."""
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._schedule_loop(),      name="scheduler_dispatch"),
            asyncio.create_task(self._lease_recovery_loop(), name="scheduler_lease"),
            asyncio.create_task(self._offline_check_loop(), name="scheduler_offline"),
        ]
        logger.info("[scheduler] Started (group=%s)", self._group_id)

    def stop(self) -> None:
        """Stop all scheduler loops gracefully."""
        self._running = False
        for t in self._tasks:
            if not t.done():
                t.cancel()
        self._tasks.clear()
        logger.info("[scheduler] Stopped")

    # ──────────────────────────────────────────────────────────────────────────
    # Dispatch loop
    # ──────────────────────────────────────────────────────────────────────────

    async def _schedule_loop(self) -> None:
        while self._running:
            try:
                await self._dispatch_pass()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("[scheduler] dispatch pass error: %s", exc)
            await asyncio.sleep(_SCHEDULER_INTERVAL)

    async def _dispatch_pass(self) -> None:
        # 1. Get available workers sorted by active_jobs ASC
        workers = await self._db.get_available_workers()
        if not workers:
            return

        workers.sort(key=lambda w: w.get("active_jobs", 0))
        total_free_slots = sum(
            max(0, w.get("capacity", 1) - w.get("active_jobs", 0))
            for w in workers
        )
        if total_free_slots == 0:
            # All workers are at capacity in the DB.  Their internal queues
            # can still absorb tasks (the worker sends ACK instead of FAILED),
            # but dispatching more right now would just bounce them.  Skip
            # this cycle; the next pass fires in _SCHEDULER_INTERVAL seconds.
            logger.debug("[scheduler] All workers at capacity — skipping dispatch pass")
            return

        # 2. Fetch queued jobs (more than slots; we'll pick fairly)
        queued = await self._db.get_queued_jobs(limit=total_free_slots * 4)
        if not queued:
            return

        # 3. Fair interleaving by user_id (round-robin across users)
        by_user: dict[int, list[dict]] = defaultdict(list)
        for job in queued:
            by_user[int(job["user_id"])].append(job)

        interleaved: list[dict] = []
        user_queues = list(by_user.values())
        while user_queues and len(interleaved) < total_free_slots:
            next_round = []
            for uq in user_queues:
                if interleaved and len(interleaved) >= total_free_slots:
                    break
                if uq:
                    interleaved.append(uq.pop(0))
                    if uq:
                        next_round.append(uq)
            user_queues = next_round

        # 4. Assign jobs to workers
        for job in interleaved:
            workers.sort(key=lambda w: w.get("active_jobs", 0))
            worker = next(
                (w for w in workers if w.get("active_jobs", 0) < w.get("capacity", 1)),
                None,
            )
            if worker is None:
                break
            await self._assign_job(job, worker)
            worker["active_jobs"] = worker.get("active_jobs", 0) + 1

    async def _assign_job(self, job: dict, worker: dict) -> None:
        """Atomically assign job to worker and send a plain-text TASK message
        to the Worker Control Group via the String Session.

        The worker receives the text TASK, then its BOT_TOKEN client fetches
        the source file via get_messages(source_chat_id, source_message_id).
        This is reliable because:
          • The worker bot must be in the same chat as the user (or the file
            was sent to the Manager bot's DM which the worker bot can also
            access via the shared source_chat_id).
          • No file forwarding through the String Session — keeps the session
            protocol-only as per the architecture contract.
        """
        job_id    = job["job_id"]
        worker_id = worker["worker_id"]

        # Atomic DB transition QUEUED → ASSIGNED
        ok = await self._db.atomic_assign_job(job_id, worker_id)
        if not ok:
            logger.debug("[scheduler] job=%s already taken — skipping", job_id)
            return

        # Build plain-text TASK proto with full source coordinates
        task_msg = make_task(
            worker_id         = worker_id,
            job_id            = job_id,
            batch_id          = job.get("batch_id", ""),
            user_id           = int(job["user_id"]),
            source_chat_id    = int(job.get("source_chat_id", 0)),
            source_message_id = int(job.get("source_message_id", 0)),
            rename_pattern    = job.get("rename_pattern", ""),
            prefix            = job.get("prefix", ""),
            suffix            = job.get("suffix", ""),
            metadata          = job.get("metadata", {}),
            metadata_version  = int(job.get("metadata_version") or 1),
            thumbnail_url     = job.get("thumbnail_url"),
            dump_enabled      = bool(job.get("dump_enabled", False)),
            upload_as         = job.get("upload_as", "document"),   # NEW
        )

        try:
            await self._session.send_message(self._group_id, task_msg)
            # Immediately reflect the assigned slot in the DB so the next
            # scheduler pass (2 s away) sees accurate counts and does NOT
            # over-dispatch to this worker.  Heartbeats will resync the
            # value every 30 s; this keeps it accurate between heartbeats.
            await self._db.inc_worker_active(worker_id)
            logger.info(
                "[scheduler] TASK sent job=%s worker=%s",
                job_id, worker_id,
            )
        except Exception as exc:
            logger.error(
                "[scheduler] Failed to send TASK job=%s worker=%s: %s",
                job_id, worker_id, exc,
            )
            await self._db.set_job_state(job_id, "QUEUED", {
                "worker_id":   None,
                "assigned_at": None,
                "lease_until": None,
            })

    # ──────────────────────────────────────────────────────────────────────────
    # Lease recovery loop
    # ──────────────────────────────────────────────────────────────────────────

    async def _lease_recovery_loop(self) -> None:
        while self._running:
            try:
                n = await self._db.requeue_expired_leases()
                if n:
                    logger.info("[scheduler] Recovered %d expired-lease jobs → QUEUED", n)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("[scheduler] lease recovery error: %s", exc)
            await asyncio.sleep(_LEASE_CHECK_INTERVAL)

    # ──────────────────────────────────────────────────────────────────────────
    # Offline detection loop
    # ──────────────────────────────────────────────────────────────────────────

    async def _offline_check_loop(self) -> None:
        from config import Config
        while self._running:
            try:
                n = await self._db.mark_workers_offline(Config.WORKER_OFFLINE_TIMEOUT)
                if n:
                    logger.info("[scheduler] Marked %d worker(s) OFFLINE", n)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("[scheduler] offline check error: %s", exc)
            await asyncio.sleep(_OFFLINE_CHECK_INTERVAL)
