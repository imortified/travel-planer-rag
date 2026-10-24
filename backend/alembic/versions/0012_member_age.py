"""Optional age profiles, independently frozen in each trip's participant snapshot."""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("memberships", sa.Column("age_profile", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("memberships", "age_profile")
