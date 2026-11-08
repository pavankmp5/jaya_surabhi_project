"""Add product import preview lifecycle and audit records.

Revision ID: 20261025_03
Revises: 20261025_02
Create Date: 2026-10-25
"""

from alembic import op
import sqlalchemy as sa


revision = "20261025_03"
down_revision = "20261025_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_import_previews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("confirmation_secret_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("proposed_rows", sa.JSON(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("confirmed_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["confirmed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_import_previews_created_by_user_id", "product_import_previews", ["created_by_user_id"], unique=False)
    op.create_index("ix_product_import_previews_expires_at", "product_import_previews", ["expires_at"], unique=False)
    op.create_index("ix_product_import_previews_status", "product_import_previews", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_product_import_previews_status", table_name="product_import_previews")
    op.drop_index("ix_product_import_previews_expires_at", table_name="product_import_previews")
    op.drop_index("ix_product_import_previews_created_by_user_id", table_name="product_import_previews")
    op.drop_table("product_import_previews")
