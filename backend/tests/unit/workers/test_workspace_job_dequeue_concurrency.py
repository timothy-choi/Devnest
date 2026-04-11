"""Concurrent dequeue: ``FOR UPDATE SKIP LOCKED`` ensures one claim per queued row (SQLite)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, select

from app.services.auth_service.models import UserAuth
from app.services.workspace_service.models import Workspace, WorkspaceJob
from app.services.workspace_service.models.enums import WorkspaceJobStatus, WorkspaceJobType, WorkspaceStatus
from app.workers.workspace_job_worker.worker import (
    try_claim_next_queued_workspace_job,
    _worker_sessionmaker,
)


@pytest.fixture
def dequeue_test_engine(tmp_path):
    """
    On-disk SQLite with ``NullPool`` so each session gets its own connection — required for
    meaningful ``FOR UPDATE SKIP LOCKED`` coverage (``StaticPool`` is single-connection).
    """
    from sqlalchemy.pool import NullPool
    from sqlmodel import SQLModel, create_engine

    db_file = tmp_path / "dequeue_concurrent.sqlite"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_owner_and_job(engine, *, owner_user_id: int | None = None) -> tuple[int, int, int]:
    """Return (owner_id, workspace_id, job_id) with one QUEUED job committed."""
    with Session(engine) as session:
        if owner_user_id is None:
            user = UserAuth(
                username="dequeue_owner",
                email="dequeue_owner@example.com",
                password_hash="x",
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            owner_user_id = user.user_auth_id
        assert owner_user_id is not None
        now = datetime.now(timezone.utc)
        ws = Workspace(
            name="dequeue-ws",
            description=None,
            owner_user_id=owner_user_id,
            status=WorkspaceStatus.CREATING.value,
            is_private=True,
            created_at=now,
            updated_at=now,
        )
        session.add(ws)
        session.flush()
        wid = ws.workspace_id
        assert wid is not None
        job = WorkspaceJob(
            workspace_id=wid,
            job_type=WorkspaceJobType.CREATE.value,
            status=WorkspaceJobStatus.QUEUED.value,
            requested_by_user_id=owner_user_id,
            requested_config_version=1,
            attempt=0,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.workspace_job_id
        assert jid is not None
        return owner_user_id, wid, jid


def test_try_claim_skip_locked_only_one_winner(dequeue_test_engine) -> None:
    """Two threads racing to claim the same QUEUED row: exactly one succeeds."""
    _seed_owner_and_job(dequeue_test_engine)
    barrier = threading.Barrier(2)
    claimed: list[int | None] = []
    lock = threading.Lock()

    sm = _worker_sessionmaker(dequeue_test_engine)

    def contender() -> None:
        barrier.wait()
        s = sm()
        try:
            job = try_claim_next_queued_workspace_job(s)
            jid = job.workspace_job_id if job is not None else None
            if job is not None:
                s.commit()
            else:
                s.rollback()
            with lock:
                claimed.append(jid)
        finally:
            s.close()

    t1 = threading.Thread(target=contender)
    t2 = threading.Thread(target=contender)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    non_null = [x for x in claimed if x is not None]
    assert len(non_null) == 1
    assert claimed.count(None) == 1

    with Session(dequeue_test_engine) as session:
        jobs = list(session.exec(select(WorkspaceJob)).all())
        assert len(jobs) == 1
        assert jobs[0].status == WorkspaceJobStatus.RUNNING.value
        assert jobs[0].attempt == 1


def test_second_claim_sees_no_queued_after_first_committed(dequeue_test_engine) -> None:
    """After one runner commits RUNNING, another dequeue finds nothing."""
    _, _, jid = _seed_owner_and_job(dequeue_test_engine)
    sm = sessionmaker(bind=dequeue_test_engine, class_=Session, expire_on_commit=False, autoflush=False)
    s1 = sm()
    try:
        j1 = try_claim_next_queued_workspace_job(s1)
        assert j1 is not None and j1.workspace_job_id == jid
        s1.commit()
    finally:
        s1.close()

    s2 = sm()
    try:
        j2 = try_claim_next_queued_workspace_job(s2)
        assert j2 is None
        s2.rollback()
    finally:
        s2.close()
