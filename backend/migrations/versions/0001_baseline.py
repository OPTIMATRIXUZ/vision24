from collections.abc import Sequence

import pgvector.sqlalchemy  # noqa: F401
import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table('tenant',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('site',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('timezone', sa.String(), nullable=False),
    sa.Column('closing_time', sa.Time(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('camera',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('site_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('rtsp_url', sa.String(), nullable=False),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['site.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('zone',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('site_id', sa.UUID(), nullable=False),
    sa.Column('camera_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('polygon', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('record_clips', sa.Boolean(), nullable=False),
    sa.Column('privacy_mask', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['camera_id'], ['camera.id'], ),
    sa.ForeignKeyConstraint(['site_id'], ['site.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('alert_rule',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('zone_id', sa.UUID(), nullable=False),
    sa.Column('metric', sa.String(), nullable=False),
    sa.Column('threshold', sa.Integer(), nullable=False),
    sa.Column('sustain_seconds', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['zone_id'], ['zone.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('event',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('camera_id', sa.UUID(), nullable=False),
    sa.Column('zone_id', sa.UUID(), nullable=True),
    sa.Column('type', sa.String(), nullable=False),
    sa.Column('track_id', sa.Integer(), nullable=True),
    sa.Column('ts_start', postgresql.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('ts_end', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.ForeignKeyConstraint(['camera_id'], ['camera.id'], ),
    sa.ForeignKeyConstraint(['zone_id'], ['zone.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_event_camera_ts', 'event', ['camera_id', 'ts_start'], unique=False)
    op.create_index('ix_event_zone_type_ts', 'event', ['zone_id', 'type', 'ts_start'], unique=False)
    op.create_table('alert',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('rule_id', sa.UUID(), nullable=False),
    sa.Column('event_id', sa.BigInteger(), nullable=True),
    sa.Column('triggered_at', postgresql.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('value', sa.Integer(), nullable=False),
    sa.Column('message', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['event.id'], ),
    sa.ForeignKeyConstraint(['rule_id'], ['alert_rule.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('clip',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('event_id', sa.BigInteger(), nullable=False),
    sa.Column('storage_key', sa.String(), nullable=True),
    sa.Column('snapshot_key', sa.String(), nullable=True),
    sa.Column('ts_start', postgresql.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('duration_s', sa.Float(), nullable=False),
    sa.Column('people_frames', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.ForeignKeyConstraint(['event_id'], ['event.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('embedding',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('event_id', sa.BigInteger(), nullable=False),
    sa.Column('vec', pgvector.sqlalchemy.vector.VECTOR(dim=512), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['event.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_embedding_event_id'), 'embedding', ['event_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_embedding_event_id'), table_name='embedding')
    op.drop_table('embedding')
    op.drop_table('clip')
    op.drop_table('alert')
    op.drop_index('ix_event_zone_type_ts', table_name='event')
    op.drop_index('ix_event_camera_ts', table_name='event')
    op.drop_table('event')
    op.drop_table('alert_rule')
    op.drop_table('zone')
    op.drop_table('camera')
    op.drop_table('site')
    op.drop_table('tenant')
