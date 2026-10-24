"""Immutable pace settings for new trip requests; NULL preserves legacy schedules."""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trip_requests", sa.Column("pace_settings", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("trip_requests", "pace_settings")
