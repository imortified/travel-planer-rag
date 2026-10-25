"""Accounts, revocable sessions and private traveller profiles; preserve legacy rows."""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_sessions_account_id", "auth_sessions", ["account_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "auth_rate_limits",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_rate_limits_expires_at", "auth_rate_limits", ["expires_at"])
    op.create_table(
        "saved_travellers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("interests", sa.JSON(), nullable=False),
        sa.Column("travel_profile", sa.JSON(), nullable=False),
        sa.Column("age_profile", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_saved_travellers_owner_account_id", "saved_travellers", ["owner_account_id"]
    )
    for table in ("users", "groups"):
        op.add_column(table, sa.Column("owner_account_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_account", table, "accounts", ["owner_account_id"], ["id"]
        )
        op.create_index(f"ix_{table}_owner_account_id", table, ["owner_account_id"])

    op.add_column("users", sa.Column("saved_traveller_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_users_saved_traveller",
        "users",
        "saved_travellers",
        ["saved_traveller_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_column("users", "saved_traveller_id")
    for table in ("users", "groups"):
        op.drop_column(table, "owner_account_id")
    for table in ("saved_travellers", "auth_rate_limits", "auth_sessions", "accounts"):
        op.drop_table(table)
