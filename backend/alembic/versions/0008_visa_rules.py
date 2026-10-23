"""Snapshot travel profiles and static visa assessments without rewriting old routes."""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("memberships", sa.Column("travel_profile", sa.JSON(), nullable=True))
    op.add_column(
        "trip_requests",
        sa.Column("purpose", sa.String(20), server_default="unknown", nullable=False),
    )
    op.add_column("route_options", sa.Column("visa_report", sa.JSON(), nullable=True))
    op.add_column("route_sets", sa.Column("visa_report", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("route_sets", "visa_report")
    op.drop_column("route_options", "visa_report")
    op.drop_column("trip_requests", "purpose")
    op.drop_column("memberships", "travel_profile")
