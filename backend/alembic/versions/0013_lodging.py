"""Immutable public lodging directory and frozen trip selections; no provider quotes."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trip_requests", sa.Column("lodging_snapshot", sa.JSON(), nullable=True))
    op.create_table(
        "lodging_catalog_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("destination", sa.String(100), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("destination", "content_sha256"),
        sa.UniqueConstraint("destination", "id"),
    )
    op.create_table(
        "lodging_catalog_heads",
        sa.Column("destination", sa.String(100), primary_key=True),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["destination", "version_id"],
            ["lodging_catalog_versions.destination", "lodging_catalog_versions.id"],
            ondelete="RESTRICT",
        ),
    )


def downgrade():
    op.drop_table("lodging_catalog_heads")
    op.drop_table("lodging_catalog_versions")
    op.drop_column("trip_requests", "lodging_snapshot")
