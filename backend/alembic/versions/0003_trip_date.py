"""Explicit local travel date for real destination opening hours."""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "3905079c877d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trip_requests", sa.Column("travel_date", sa.Date(), nullable=True))


def downgrade():
    op.drop_column("trip_requests", "travel_date")
