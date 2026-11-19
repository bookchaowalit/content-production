"""SPEC-001 R-QUEUE / R-LEASE: blocked stages, lease tokens, one-commit transitions and recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.test_stage_runner import FailingProvider, FakeProbe, WorkingProvider, make_asset
from video_postproduction.config import Settings
from video_postproduction.jobs.queue import InlineJobQueue
from video_postproduction.jobs.recovery import recover_jobs
from video_postproduction.jobs.stages import StageRunner
from video_postproduction.models import ProcessingJob, ProcessingStage


class RecordingQueue:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[object, str]] = []
        self.fail = fail

    def enqueue(self, job_id, stage_name: str) -> None:
        if self.fail:
            raise ConnectionError("broker down")
        self.calls.append((job_id, stage_name))


def ingest_job(
    session,
    asset,
    *,
    probe: str = "queued",
    transcribe: str = "blocked",
    job_status: str = "queued",
):
    job = ProcessingJob(
        project_id=asset.project_id,
        asset_id=asset.id,
        kind="ingest",
        status=job_status,
        idempotency_key=f"ingest:{asset.id}",
    )
    session.add(job)
    session.flush()
    session.add(ProcessingStage(job_id=job.id, name="probe", status=probe))
    session.add(ProcessingStage(job_id=job.id, name="transcribe", status=transcribe))
    session.commit()
    return job


def stage(session, job, name: str) -> ProcessingStage:
    row = session.query(ProcessingStage).filter_by(job_id=job.id, name=name).one()
    session.refresh(row)
    return row


def runner(session, storage, *, provider=None, queue=None, settings=None) -> StageRunner:
    return StageRunner(
        session,
        storage=storage,
        probe_reader=FakeProbe(),
        transcription_provider=provider or WorkingProvider(),
        settings=settings or Settings(max_stage_attempts=3),
        queue=queue if queue is not None else RecordingQueue(),
    )


def test_probe_hand_off_queues_transcribe_in_the_same_commit(session, tmp_path) -> None:
    _, asset, storage = make_asset(session, tmp_path)
    job = ingest_job(session, asset)
    queue = RecordingQueue()
    runner(session, storage, queue=queue).run_probe(job.id)

    probe, transcribe = stage(session, job, "probe"), stage(session, job, "transcribe")
    assert probe.status == "succeeded"
    assert probe.lease_token is None
    assert transcribe.status == "queued"
    assert transcribe.enqueued_at is not None
    assert queue.calls == [(job.id, "transcribe")]


def test_failed_enqueue_leaves_the_stage_recoverable(session, tmp_path) -> None:
    _, asset, storage = make_asset(session, tmp_path)
    job = ingest_job(session, asset)
    runner(session, storage, queue=RecordingQueue(fail=True)).run_probe(job.id)

    transcribe = stage(session, job, "transcribe")
    session.refresh(job)
    assert transcribe.status == "queued"
    assert transcribe.enqueued_at is None
    assert job.status != "failed"

    queue = RecordingQueue()
    assert recover_jobs(session, Settings(), queue) >= 1
    assert queue.calls == [(job.id, "transcribe")]
    assert stage(session, job, "transcribe").enqueued_at is not None


def test_early_transcribe_message_is_a_no_op(session, tmp_path) -> None:
    _, asset, storage = make_asset(session, tmp_path)
    job = ingest_job(session, asset)
    runner(session, storage).run_transcribe(job.id)
    assert stage(session, job, "transcribe").status == "blocked"
    assert stage(session, job, "probe").status == "queued"


def test_duplicate_message_for_a_running_stage_is_a_no_op(session, tmp_path) -> None:
    _, asset, storage = make_asset(session, tmp_path)
    job = ingest_job(session, asset, probe="running")
    runner(session, storage).run_probe(job.id)
    assert stage(session, job, "probe").status == "running"


def test_final_stage_and_job_finish_together(session, tmp_path) -> None:
    _, asset, storage = make_asset(session, tmp_path)
    asset.metadata_json = {"duration": 2, "video": {"width": 320, "height": 240, "fps": 30}}
    job = ingest_job(session, asset, probe="succeeded", transcribe="queued", job_status="running")
    runner(session, storage).run_transcribe(job.id)
    session.refresh(job)
    assert stage(session, job, "transcribe").status == "succeeded"
    assert job.status == "succeeded"
    assert job.finished_at is not None


def test_failure_skips_later_stages_and_retry_brings_them_back(session, tmp_path) -> None:
    class BrokenProbe:
        def read(self, path):
            from video_postproduction.media.ffprobe import MediaProbeError

            raise MediaProbeError("ffprobe_failed", "not a video")

    _, asset, storage = make_asset(session, tmp_path)
    job = ingest_job(session, asset)
    broken = runner(session, storage)
    broken.probe_reader = BrokenProbe()
    broken.run_probe(job.id)
    session.refresh(job)
    assert stage(session, job, "probe").status == "failed"
    assert stage(session, job, "transcribe").status == "skipped"
    assert job.status == "failed"

    queue = RecordingQueue()
    retried = runner(session, storage, queue=queue).retry_stage(job.id, "probe")
    assert retried.status == "retry_queued"
    assert stage(session, job, "probe").status == "queued"
    assert stage(session, job, "transcribe").status == "blocked"
    assert queue.calls == [(job.id, "probe")]


def test_worker_that_lost_its_lease_writes_nothing(session, tmp_path) -> None:
    _, asset, storage = make_asset(session, tmp_path)
    asset.metadata_json = {"duration": 2, "video": {"width": 320, "height": 240, "fps": 30}}
    job = ingest_job(session, asset, probe="succeeded", transcribe="queued", job_status="running")

    class StallingProvider(WorkingProvider):
        def transcribe(self, audio_path, metadata, language=None):
            # While this worker is stuck, its heartbeat goes stale and recovery takes the stage over.
            later = datetime.now(UTC) + timedelta(minutes=10)
            recover_jobs(session, Settings(max_stage_attempts=3), RecordingQueue(), now=later)
            return super().transcribe(audio_path, metadata, language)

    runner(session, storage, provider=StallingProvider()).run_transcribe(job.id)

    transcribe = stage(session, job, "transcribe")
    session.refresh(job)
    assert transcribe.status == "queued"  # recovery's state, not the stalled worker's "succeeded"
    assert job.status == "retry_queued"
    assert asset.status != "transcribed"


def test_lease_expiry_on_the_last_attempt_fails_the_job(session, tmp_path) -> None:
    _, asset, _ = make_asset(session, tmp_path)
    job = ingest_job(session, asset, probe="running", job_status="running")
    probe = stage(session, job, "probe")
    probe.attempt = 3
    probe.lease_token = "old-token"
    probe.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
    session.commit()

    recover_jobs(session, Settings(max_stage_attempts=3), RecordingQueue())

    session.refresh(job)
    assert stage(session, job, "probe").status == "failed"
    assert stage(session, job, "probe").error_code == "lease_expired"
    assert stage(session, job, "transcribe").status == "skipped"
    assert job.status == "failed"


def test_live_heartbeat_is_never_taken_over(session, tmp_path) -> None:
    _, asset, _ = make_asset(session, tmp_path)
    job = ingest_job(session, asset, probe="running", job_status="running")
    probe = stage(session, job, "probe")
    probe.lease_token = "live"
    probe.heartbeat_at = datetime.now(UTC) - timedelta(seconds=30)
    probe.started_at = datetime.now(UTC) - timedelta(hours=2)  # long stage, fresh heartbeat
    session.commit()

    recover_jobs(session, Settings(), RecordingQueue())
    assert stage(session, job, "probe").status == "running"
    assert stage(session, job, "probe").lease_token == "live"


def test_blocked_stage_after_a_finished_one_is_healed(session, tmp_path) -> None:
    _, asset, _ = make_asset(session, tmp_path)
    job = ingest_job(session, asset, probe="succeeded", transcribe="blocked", job_status="running")
    queue = RecordingQueue()
    recover_jobs(session, Settings(), queue)
    assert stage(session, job, "transcribe").status == "queued"
    assert queue.calls == [(job.id, "transcribe")]


def test_inline_pipeline_still_runs_end_to_end(session, tmp_path) -> None:
    _, asset, storage = make_asset(session, tmp_path)
    asset.metadata_json = {}
    job = ingest_job(session, asset)
    inline = InlineJobQueue()
    stage_runner = runner(session, storage, queue=inline)
    inline.runner = stage_runner
    inline.enqueue(job.id, "probe")
    session.refresh(job)
    assert job.status == "succeeded"
    assert [name for _, name in inline.calls] == ["probe", "transcribe"]


def test_failing_provider_marks_the_job_failed_once(session, tmp_path) -> None:
    _, asset, storage = make_asset(session, tmp_path)
    asset.metadata_json = {"duration": 2, "video": {"width": 320, "height": 240, "fps": 30}}
    job = ingest_job(session, asset, probe="succeeded", transcribe="queued", job_status="running")
    runner(session, storage, provider=FailingProvider()).run_transcribe(job.id)
    session.refresh(job)
    assert stage(session, job, "transcribe").status == "failed"
    assert stage(session, job, "transcribe").lease_token is None
    assert job.status == "failed"


def test_retranscription_job_runs_without_a_probe_stage(session, tmp_path) -> None:
    _, asset, storage = make_asset(session, tmp_path)
    asset.metadata_json = {"duration": 2, "video": {"width": 320, "height": 240, "fps": 30}}
    job = ProcessingJob(
        project_id=asset.project_id,
        asset_id=asset.id,
        kind="retranscription",
        status="queued",
        idempotency_key=f"retranscription:{asset.id}",
    )
    session.add(job)
    session.flush()
    session.add(ProcessingStage(job_id=job.id, name="transcribe", status="queued"))
    session.commit()
    runner(session, storage).run_transcribe(job.id)
    session.refresh(job)
    assert stage(session, job, "transcribe").status == "succeeded"
    assert job.status == "succeeded"


def test_stalled_worker_writes_no_transcript_rows(session, tmp_path) -> None:
    from video_postproduction.models import Transcript

    _, asset, storage = make_asset(session, tmp_path)
    asset.metadata_json = {"duration": 2, "video": {"width": 320, "height": 240, "fps": 30}}
    job = ingest_job(session, asset, probe="succeeded", transcribe="queued", job_status="running")

    class StallingProvider(WorkingProvider):
        def transcribe(self, audio_path, metadata, language=None):
            later = datetime.now(UTC) + timedelta(minutes=10)
            recover_jobs(session, Settings(max_stage_attempts=3), RecordingQueue(), now=later)
            return super().transcribe(audio_path, metadata, language)

    runner(session, storage, provider=StallingProvider()).run_transcribe(job.id)
    assert session.query(Transcript).count() == 0


def test_recovery_leaves_render_stages_alone(session, tmp_path) -> None:
    _, asset, _ = make_asset(session, tmp_path)
    job = ProcessingJob(
        project_id=asset.project_id,
        asset_id=asset.id,
        kind="render",
        status="running",
        idempotency_key=f"render:{asset.id}",
    )
    session.add(job)
    session.flush()
    session.add(ProcessingStage(job_id=job.id, name="render", status="running"))  # no lease yet
    session.commit()
    recover_jobs(session, Settings(), RecordingQueue(), now=datetime.now(UTC) + timedelta(hours=1))
    session.refresh(job)
    assert stage(session, job, "render").status == "running"
    assert job.status == "running"


def test_takeover_is_refused_when_the_worker_finished_meanwhile(session, tmp_path) -> None:
    from video_postproduction.jobs.recovery import take_over_stale_stage

    _, asset, _ = make_asset(session, tmp_path)
    job = ingest_job(session, asset, probe="running", job_status="running")
    probe = stage(session, job, "probe")
    probe.lease_token = "old"
    probe.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
    session.commit()
    seen_token, seen_heartbeat = probe.lease_token, probe.heartbeat_at
    # The worker commits its result after recovery read the row but before recovery writes.
    probe.status = "succeeded"
    probe.lease_token = None
    session.commit()

    assert take_over_stale_stage(session, probe.id, seen_token, seen_heartbeat, retry=True) is False
    session.commit()
    assert stage(session, job, "probe").status == "succeeded"


def test_heartbeat_thread_keeps_a_long_stage_alive(tmp_path) -> None:
    import time

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as OrmSession

    from video_postproduction.db import Base

    engine = create_engine(f"sqlite:///{(tmp_path / 'hb.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with OrmSession(engine, expire_on_commit=False) as session:
        _, asset, storage = make_asset(session, tmp_path)
        asset.metadata_json = {"duration": 2, "video": {"width": 320, "height": 240, "fps": 30}}
        job = ingest_job(session, asset, probe="succeeded", transcribe="queued", job_status="running")
        beats: list[datetime] = []

        class SlowProvider(WorkingProvider):
            def transcribe(self, audio_path, metadata, language=None):
                time.sleep(0.4)
                with OrmSession(engine) as other:
                    beats.append(other.get(ProcessingStage, stage(session, job, "transcribe").id).heartbeat_at)
                return super().transcribe(audio_path, metadata, language)

        runner(session, storage, provider=SlowProvider(), settings=Settings(stage_heartbeat_seconds=0.1)).run_transcribe(
            job.id
        )
        claimed = stage(session, job, "transcribe").started_at
        assert stage(session, job, "transcribe").status == "succeeded"
    engine.dispose()
    assert beats and beats[0] is not None
    assert beats[0].replace(tzinfo=None) > claimed.replace(tzinfo=None)
