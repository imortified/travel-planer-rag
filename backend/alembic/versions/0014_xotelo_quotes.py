"""Bind short-lived provider observations to a group before freezing them in a trip."""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lodging_provider_quotes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lodging_provider_quotes_group_id", "lodging_provider_quotes", ["group_id"])


def downgrade():
    op.drop_table("lodging_provider_quotes")
