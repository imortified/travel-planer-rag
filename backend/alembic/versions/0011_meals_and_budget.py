"""Freeze user budget estimates and daily meal settings; preserve historical requests."""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trip_requests", sa.Column("budget_settings", sa.JSON(), nullable=True))
    op.add_column("trip_requests", sa.Column("meal_settings", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("trip_requests", "meal_settings")
    op.drop_column("trip_requests", "budget_settings")
