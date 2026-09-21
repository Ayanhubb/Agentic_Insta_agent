"""Record scheduled job runs without calling Instagram directly."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from db.repositories import JobRepository


class JobManager:
    def __init__(self, session: Session) -> None:
        self._jobs = JobRepository(session)

    def mark_run(self, user_id: str, job_type: str, *, status: str = "ran") -> None:
        label = "FESTIVAL_CAMPAIGN" if job_type in {"FESTIVAL", "FESTIVAL_CAMPAIGN"} else (
            "DAILY_RETAIL_POST" if job_type in {"DAILY", "DAILY_RETAIL_POST"} else str(job_type)
        )
        self._jobs.upsert(
            user_id,
            label,
            last_run_at=datetime.now(timezone.utc),
            status=status,
            enabled=True,
        )
