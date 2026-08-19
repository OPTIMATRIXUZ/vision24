from collections.abc import Sequence

import pgvector.sqlalchemy  # noqa: F401
import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_job",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("camera_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("events_written", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("runtime_id", sa.String(length=32), nullable=False),
        sa.Column("queued_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["camera_id"], ["camera.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_analysis_job_camera_id"), "analysis_job", ["camera_id"])
    op.create_index("ix_analysis_job_camera_queued", "analysis_job", ["camera_id", "queued_at"])


def downgrade() -> None:
    op.drop_index("ix_analysis_job_camera_queued", table_name="analysis_job")
    op.drop_index(op.f("ix_analysis_job_camera_id"), table_name="analysis_job")
    op.drop_table("analysis_job")
