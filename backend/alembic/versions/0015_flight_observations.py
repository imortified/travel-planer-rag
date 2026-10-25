"""Freeze flight observations independently from lodging and ground transport estimates."""

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trip_requests", sa.Column("flight_snapshot", sa.JSON(), nullable=True))
    op.create_table(
        "flight_search_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_flight_search_observations_group_id", "flight_search_observations", ["group_id"]
    )


def downgrade():
    op.drop_table("flight_search_observations")
    op.drop_column("trip_requests", "flight_snapshot")
