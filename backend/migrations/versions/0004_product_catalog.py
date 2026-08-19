from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_type",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("site_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("units_per_package", sa.Integer(), nullable=True),
        sa.Column("unit_label", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["site_id"], ["site.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("site_id", "name", name="uq_product_type_site_name"),
    )
    op.create_index(op.f("ix_product_type_site_id"), "product_type", ["site_id"])
    op.create_table(
        "product_sample",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("product_type_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["product_type_id"], ["product_type.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_product_sample_product_type_id"), "product_sample", ["product_type_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_product_sample_product_type_id"), table_name="product_sample")
    op.drop_table("product_sample")
    op.drop_index(op.f("ix_product_type_site_id"), table_name="product_type")
    op.drop_table("product_type")
