from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("site", sa.Column("telegram_chat_id", sa.String(), nullable=True))
    op.add_column(
        "site",
        sa.Column("telegram_enabled", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column("site", sa.Column("telegram_digest_time", sa.Time(), nullable=True))
    op.add_column("site", sa.Column("telegram_digest_last_sent_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("site", "telegram_digest_last_sent_on")
    op.drop_column("site", "telegram_digest_time")
    op.drop_column("site", "telegram_enabled")
    op.drop_column("site", "telegram_chat_id")
