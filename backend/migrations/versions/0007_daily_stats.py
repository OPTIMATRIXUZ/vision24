from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column("stats_opt_in", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_table(
        "site_daily_stats",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("site_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("visitors", sa.Integer(), nullable=False),
        sa.Column("peak_occupancy", sa.Integer(), nullable=False),
        sa.Column("peak_hour", sa.Integer(), nullable=True),
        sa.Column("avg_dwell_s", sa.Float(), nullable=True),
        sa.Column("queue_breaches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alerts_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["site_id"], ["site.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("site_id", "day", name="uq_site_daily_stats_site_day"),
    )
    op.create_index(op.f("ix_site_daily_stats_site_id"), "site_daily_stats", ["site_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_site_daily_stats_site_id"), table_name="site_daily_stats")
    op.drop_table("site_daily_stats")
    op.drop_column("tenant", "stats_opt_in")
