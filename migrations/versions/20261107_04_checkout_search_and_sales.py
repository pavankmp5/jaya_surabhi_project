"""Add checkout search fields and sale snapshots.

Revision ID: 20261107_04
Revises: 20261025_03
Create Date: 2026-11-07
"""

from alembic import op
import sqlalchemy as sa

revision = "20261107_04"
down_revision = "20261025_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("category", sa.String(length=80), nullable=True))
    op.add_column("products", sa.Column("mrp", sa.Numeric(precision=12, scale=2), nullable=True))
    op.create_index("ix_products_category", "products", ["category"], unique=False)
    op.create_table(
        "sales",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_created_by_user_id", "sales", ["created_by_user_id"], unique=False)
    op.create_index("ix_sales_status", "sales", ["status"], unique=False)
    op.create_table(
        "sale_lines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sale_id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=True),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("is_temporary", sa.Boolean(), nullable=False),
        sa.Column("description_snapshot", sa.String(length=200), nullable=False),
        sa.Column("sku_snapshot", sa.String(length=80), nullable=True),
        sa.Column("unit_snapshot", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("mrp_snapshot", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("unit_price_snapshot", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("tax_classification_snapshot", sa.String(length=64), nullable=True),
        sa.Column("tax_classification_status_snapshot", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sale_lines_product_id", "sale_lines", ["product_id"], unique=False)
    op.create_index("ix_sale_lines_sale_id", "sale_lines", ["sale_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sale_lines_sale_id", table_name="sale_lines")
    op.drop_index("ix_sale_lines_product_id", table_name="sale_lines")
    op.drop_table("sale_lines")
    op.drop_index("ix_sales_status", table_name="sales")
    op.drop_index("ix_sales_created_by_user_id", table_name="sales")
    op.drop_table("sales")
    op.drop_index("ix_products_category", table_name="products")
    op.drop_column("products", "mrp")
    op.drop_column("products", "category")
