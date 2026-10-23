"""Inclusive end date; legacy trips remain single-day."""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trip_requests", sa.Column("end_date", sa.Date(), nullable=True))
    op.create_check_constraint(
        "ck_trip_period",
        "trip_requests",
        "end_date IS NULL OR (travel_date IS NOT NULL AND end_date - travel_date BETWEEN 0 AND 13)",
    )


def downgrade():
    op.drop_constraint("ck_trip_period", "trip_requests", type_="check")
    op.drop_column("trip_requests", "end_date")
