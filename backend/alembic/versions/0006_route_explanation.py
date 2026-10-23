"""Store verified explanations without changing route snapshots."""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("route_options", sa.Column("explanation", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("route_options", "explanation")
