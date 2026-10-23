"""Persist optional semantic intent; retrieved documents live in route snapshots."""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trip_requests", sa.Column("semantic_query", sa.String(300), nullable=True))


def downgrade():
    op.drop_column("trip_requests", "semantic_query")
