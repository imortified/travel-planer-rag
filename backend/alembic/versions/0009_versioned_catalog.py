"""versioned_catalog"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "catalog_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("destination", sa.String(length=100), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fresh_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("poi_count", sa.Integer(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.CheckConstraint("fresh_until > fetched_at", name="ck_catalog_freshness"),
        sa.CheckConstraint("poi_count > 0", name="ck_catalog_count"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("destination", "id"),
    )
    op.create_index(
        op.f("ix_catalog_versions_destination"), "catalog_versions", ["destination"], unique=False
    )
    op.create_table(
        "catalog_heads",
        sa.Column("destination", sa.String(length=100), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["destination", "version_id"],
            ["catalog_versions.destination", "catalog_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("destination"),
    )
    op.create_table(
        "catalog_pois",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("place_id", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("cost_per_person", sa.Integer(), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=False),
        sa.Column("opening_hours", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "cost_per_person IS NULL OR cost_per_person >= 0", name="ck_catalog_price"
        ),
        sa.CheckConstraint("duration BETWEEN 1 AND 1440", name="ck_catalog_duration"),
        sa.CheckConstraint(
            "latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180",
            name="ck_catalog_coordinates",
        ),
        sa.ForeignKeyConstraint(["version_id"], ["catalog_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("version_id", "place_id"),
    )


def downgrade():
    op.drop_table("catalog_pois")
    op.drop_table("catalog_heads")
    op.drop_index(op.f("ix_catalog_versions_destination"), table_name="catalog_versions")
    op.drop_table("catalog_versions")
