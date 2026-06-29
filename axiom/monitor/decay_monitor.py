"""
AXIOM Skill Decay Monitor

APScheduler background job that continuously monitors the health of active skills.

Decay conditions checked:
  1. Performance decay: >50 invocations AND success_rate < 0.70 → FLAGGED
  2. Idle decay:        not invoked in 30 days → DEPRECATED

When decay is detected, the monitor:
  - Updates skill status in the registry
  - Logs a structured event for observability
  - (Optional) Triggers re-synthesis for performance-decayed skills
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from axiom.config import settings
from axiom.models import Skill, SkillStatus
from axiom.registry.skill_store import skill_store


class SkillDecayMonitor:
    """
    APScheduler-backed monitor that runs periodic health checks on all active skills.

    Usage:
        monitor = SkillDecayMonitor()
        monitor.start()  # call during FastAPI startup
        ...
        monitor.stop()   # call during FastAPI shutdown
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._running = False
        self._last_run: Optional[datetime] = None
        self._flagged_count = 0
        self._deprecated_count = 0

    def start(self) -> None:
        """Start the decay monitor scheduler."""
        self._scheduler.add_job(
            self._run_decay_check,
            trigger=IntervalTrigger(minutes=settings.decay_check_interval_minutes),
            id="skill_decay_check",
            name="AXIOM Skill Decay Monitor",
            replace_existing=True,
            max_instances=1,
        )
        self._scheduler.start()
        self._running = True
        logger.info(
            f"Decay monitor started — checking every {settings.decay_check_interval_minutes} min"
        )

    def stop(self) -> None:
        """Stop the decay monitor scheduler."""
        if self._running:
            self._scheduler.shutdown(wait=False)
            self._running = False
            logger.info("Decay monitor stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "total_flagged": self._flagged_count,
            "total_deprecated": self._deprecated_count,
            "check_interval_minutes": settings.decay_check_interval_minutes,
        }

    # ── Core Check ────────────────────────────────────────────────────────────

    async def _run_decay_check(self) -> None:
        """
        Main decay check — called by the scheduler.
        Checks all active skills for performance and idle decay.
        """
        self._last_run = datetime.now(timezone.utc)
        logger.info("Decay monitor: starting health check cycle")

        flagged_this_run = 0
        deprecated_this_run = 0

        # ── Performance Decay ─────────────────────────────────────────────────
        try:
            decay_candidates = await skill_store.get_skills_for_decay_check()
            logger.debug(f"Checking {len(decay_candidates)} skills for performance decay")

            for skill in decay_candidates:
                if await self._check_performance_decay(skill):
                    flagged_this_run += 1

        except Exception as exc:
            logger.error(f"Decay monitor: performance check failed: {exc}")

        # ── Idle Decay ────────────────────────────────────────────────────────
        try:
            idle_skills = await skill_store.get_idle_skills(idle_days=settings.decay_idle_days)
            logger.debug(f"Checking {len(idle_skills)} skills for idle decay")

            for skill in idle_skills:
                if await self._check_idle_decay(skill):
                    deprecated_this_run += 1

        except Exception as exc:
            logger.error(f"Decay monitor: idle check failed: {exc}")

        # ── Summary ───────────────────────────────────────────────────────────
        self._flagged_count += flagged_this_run
        self._deprecated_count += deprecated_this_run

        logger.info(
            f"Decay monitor cycle complete — "
            f"flagged={flagged_this_run}, deprecated={deprecated_this_run}"
        )

    async def _check_performance_decay(self, skill: Skill) -> bool:
        """
        Check a single skill for performance decay.
        Returns True if the skill was flagged.
        """
        if skill.success_rate < settings.decay_success_threshold:
            logger.warning(
                f"Performance decay detected: '{skill.name}' "
                f"success_rate={skill.success_rate:.2%} "
                f"(threshold={settings.decay_success_threshold:.2%}) "
                f"invocations={skill.invocation_count}"
            )

            await skill_store.update_skill_status(skill.id, SkillStatus.FLAGGED)

            # Optional: trigger re-synthesis
            if settings.decay_success_threshold > 0:
                await self._maybe_trigger_resynthesis(skill)

            return True
        return False

    async def _check_idle_decay(self, skill: Skill) -> bool:
        """
        Check a single skill for idle decay.
        Returns True if the skill was deprecated.
        """
        logger.info(
            f"Idle decay: '{skill.name}' has not been invoked in >{settings.decay_idle_days} days "
            f"(last_invoked={skill.last_invoked_at})"
        )
        await skill_store.update_skill_status(skill.id, SkillStatus.DEPRECATED)
        return True

    async def _maybe_trigger_resynthesis(self, skill: Skill) -> None:
        """
        Optionally trigger re-synthesis of a decayed skill.
        Currently logs intent; actual synthesis is triggered by the API layer
        to avoid circular imports and allow async streaming.
        """
        logger.info(
            f"Re-synthesis candidate: '{skill.name}' (id={skill.id}) "
            f"will be queued for re-synthesis on next API call"
        )
        # In a production system this would enqueue to a Redis job queue
        # e.g.: await redis.rpush("axiom:resynthesis_queue", skill.id)

    # ── Manual triggers ───────────────────────────────────────────────────────

    async def run_now(self) -> dict:
        """Manually trigger an immediate decay check (useful for testing/debugging)."""
        logger.info("Decay monitor: manual trigger")
        await self._run_decay_check()
        return self.stats


# Module-level singleton
decay_monitor = SkillDecayMonitor()
