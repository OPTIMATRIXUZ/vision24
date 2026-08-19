from collections.abc import Sequence

import pgvector.sqlalchemy  # noqa: F401
import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "site",
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.add_column("tenant", sa.Column("slug", sa.String(), nullable=True))

    op.execute("UPDATE tenant SET slug = 'demo' WHERE name = 'demo' AND slug IS NULL")
    op.execute(
        "UPDATE tenant SET slug = trim(both '-' from "
        "regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g')) WHERE slug IS NULL"
    )
    op.execute(
        "UPDATE tenant SET slug = 'tenant-' || left(id::text, 8) "
        "WHERE slug IS NULL OR slug = ''"
    )
    op.execute(
        "UPDATE tenant t SET slug = t.slug || '-' || left(t.id::text, 8) "
        "WHERE EXISTS (SELECT 1 FROM tenant o WHERE o.slug = t.slug AND o.id <> t.id)"
    )

    op.alter_column("tenant", "slug", nullable=False)
    op.create_unique_constraint("uq_tenant_slug", "tenant", ["slug"])

    op.execute("ALTER TABLE tenant DROP CONSTRAINT IF EXISTS tenant_name_key")

    op.create_table(
        "app_user",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(op.f("ix_app_user_tenant_id"), "app_user", ["tenant_id"], unique=False)

    op.create_table(
        "user_session",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(), nullable=False),
        sa.Column(
            "issued_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.UUID(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("ip", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["replaced_by_id"], ["user_session.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("refresh_token_hash"),
    )
    op.create_index(op.f("ix_user_session_user_id"), "user_session", ["user_id"], unique=False)

    op.create_table(
        "api_key",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("prefix", sa.String(length=8), nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["site_id"], ["site.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_key_tenant_id"), "api_key", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_api_key_prefix"), "api_key", ["prefix"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_api_key_prefix"), table_name="api_key")
    op.drop_index(op.f("ix_api_key_tenant_id"), table_name="api_key")
    op.drop_table("api_key")
    op.drop_index(op.f("ix_user_session_user_id"), table_name="user_session")
    op.drop_table("user_session")
    op.drop_index(op.f("ix_app_user_tenant_id"), table_name="app_user")
    op.drop_table("app_user")

    op.create_unique_constraint("tenant_name_key", "tenant", ["name"])
    op.drop_constraint("uq_tenant_slug", "tenant", type_="unique")
    op.drop_column("tenant", "slug")
    op.drop_column("site", "created_at")
