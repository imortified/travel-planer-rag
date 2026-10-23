"""Persist each comparison as a set; legacy route rows remain independent."""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "route_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "trip_request_id", sa.Integer(), sa.ForeignKey("trip_requests.id"), nullable=False
        ),
        sa.Column("algorithm", sa.String(50), nullable=False),
        sa.Column("search_info", sa.JSON(), nullable=False),
        sa.Column("diagnostic", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_route_sets_trip_request_id", "route_sets", ["trip_request_id"])
    op.add_column("route_options", sa.Column("route_set_id", sa.Integer(), nullable=True))
    op.add_column("route_options", sa.Column("comparison", sa.JSON(), nullable=True))
    op.create_foreign_key(
        "fk_route_options_route_set", "route_options", "route_sets", ["route_set_id"], ["id"]
    )
    op.create_index("ix_route_options_route_set_id", "route_options", ["route_set_id"])


def downgrade():
    op.drop_index("ix_route_options_route_set_id", table_name="route_options")
    op.drop_constraint("fk_route_options_route_set", "route_options", type_="foreignkey")
    op.drop_column("route_options", "comparison")
    op.drop_column("route_options", "route_set_id")
    op.drop_index("ix_route_sets_trip_request_id", table_name="route_sets")
    op.drop_table("route_sets")
