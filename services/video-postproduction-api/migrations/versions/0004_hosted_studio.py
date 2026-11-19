"""SPEC-001: hosted-studio columns and repair of rows written by the old pipeline.

Additive only (SPEC-001 R-REL-2): widens ``media_assets.size_bytes``, adds nullable columns, and repairs states so
the new stage rules (R-QUEUE, R-LEASE) can pick up work the old worker left behind.
"""

from __future__ import annotations

from itertools import pairwise

import sqlalchemy as sa
from alembic import op

revision = "0004_hosted_studio"
down_revision = "0003_transcription_quality"
branch_labels = None
depends_on = None

# Order of stages inside one ingest job; render jobs have a single "render" stage.
INGEST_ORDER = ("probe", "transcribe")


def upgrade() -> None:
    with op.batch_alter_table("media_assets") as batch:
        batch.alter_column(
            "size_bytes", existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=False
        )
        batch.add_column(sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("render_outputs") as batch:
        batch.add_column(sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("processing_stages") as batch:
        batch.add_column(sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("lease_token", sa.String(length=36), nullable=True))
    with op.batch_alter_table("transcripts") as batch:
        batch.add_column(sa.Column("quality", sa.String(length=20), nullable=True))

    connection = op.get_bind()
    stages = sa.table(
        "processing_stages",
        sa.column("job_id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("status", sa.String()),
        sa.column("heartbeat_at", sa.DateTime(timezone=True)),
    )
    jobs = sa.table(
        "processing_jobs",
        sa.column("id", sa.Uuid()),
        sa.column("kind", sa.String()),
        sa.column("status", sa.String()),
        sa.column("finished_at", sa.DateTime(timezone=True)),
    )
    renders = sa.table(
        "render_outputs",
        sa.column("job_id", sa.Uuid()),
        sa.column("status", sa.String()),
        sa.column("storage_key", sa.String()),
    )
    now = sa.func.current_timestamp()

    # A queued stage whose previous stage has not succeeded waits for the hand-off (R-QUEUE).
    previous = stages.alias("previous")
    for earlier, later in pairwise(INGEST_ORDER):
        not_done = (
            sa.select(sa.literal(1))
            .select_from(previous)
            .where(
                previous.c.job_id == stages.c.job_id,
                previous.c.name == earlier,
                previous.c.status != "succeeded",
            )
            .exists()
        )
        connection.execute(
            stages.update()
            .where(stages.c.name == later, stages.c.status == "queued", not_done)
            .values(status="blocked")
        )

    # A running stage gets a heartbeat so lease recovery can take it over if nothing is running (R-LEASE).
    connection.execute(
        stages.update()
        .where(stages.c.status == "running", stages.c.heartbeat_at.is_(None))
        .values(heartbeat_at=now)
    )

    # Ingest or retranscription job whose last stage succeeded but whose own commit never happened (fact 19).
    last_done = (
        sa.select(sa.literal(1))
        .where(
            stages.c.job_id == jobs.c.id,
            stages.c.name == INGEST_ORDER[-1],
            stages.c.status == "succeeded",
        )
        .exists()
    )
    connection.execute(
        jobs.update()
        .where(jobs.c.kind.in_(("ingest", "retranscription")), jobs.c.status == "running", last_done)
        .values(status="succeeded", finished_at=now)
    )

    # Render job whose output row committed succeeded before its stage and job (fact 13).
    render_done = (
        sa.select(sa.literal(1))
        .where(
            renders.c.job_id == jobs.c.id,
            renders.c.status == "succeeded",
            renders.c.storage_key.is_not(None),
        )
        .exists()
    )
    render_jobs = sa.select(jobs.c.id).where(
        jobs.c.kind == "render", jobs.c.status == "running", render_done
    )
    connection.execute(
        stages.update()
        .where(
            stages.c.job_id.in_(render_jobs.scalar_subquery()),
            stages.c.name == "render",
            stages.c.status == "running",
        )
        .values(status="succeeded")
    )
    connection.execute(
        jobs.update()
        .where(jobs.c.kind == "render", jobs.c.status == "running", render_done)
        .values(status="succeeded", finished_at=now)
    )


def downgrade() -> None:
    # Development and CI only (R-REL-2): production rollback never downgrades.
    connection = op.get_bind()
    connection.execute(
        sa.text("UPDATE processing_stages SET status = 'queued' WHERE status = 'blocked'")
    )
    with op.batch_alter_table("transcripts") as batch:
        batch.drop_column("quality")
    with op.batch_alter_table("processing_stages") as batch:
        batch.drop_column("lease_token")
        batch.drop_column("heartbeat_at")
        batch.drop_column("enqueued_at")
    with op.batch_alter_table("render_outputs") as batch:
        batch.drop_column("purged_at")
    with op.batch_alter_table("media_assets") as batch:
        batch.drop_column("purged_at")
        batch.alter_column(
            "size_bytes", existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False
        )
