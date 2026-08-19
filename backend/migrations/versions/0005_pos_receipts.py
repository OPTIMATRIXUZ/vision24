from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pos_receipt",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("site_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("zone_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items", JSONB(), nullable=False, server_default="[]"),
        sa.Column("source", sa.String(), nullable=False, server_default="api"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["site_id"], ["site.id"]),
        sa.ForeignKeyConstraint(["zone_id"], ["zone.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("site_id", "external_id", name="uq_pos_receipt_site_external"),
    )
    op.create_index(op.f("ix_pos_receipt_site_id"), "pos_receipt", ["site_id"])
    op.create_index("ix_pos_receipt_site_ts", "pos_receipt", ["site_id", "ts"])


def downgrade() -> None:
    op.drop_index("ix_pos_receipt_site_ts", table_name="pos_receipt")
    op.drop_index(op.f("ix_pos_receipt_site_id"), table_name="pos_receipt")
    op.drop_table("pos_receipt")
