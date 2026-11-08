"""Add payments, receipt numbering, and customer khata.

Revision ID: 20261108_05
Revises: 20261107_04
Create Date: 2026-11-08
"""

from alembic import op
import sqlalchemy as sa

revision = "20261108_05"
down_revision = "20261107_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sales", sa.Column("receipt_number", sa.String(length=80), nullable=True))
    op.add_column("sales", sa.Column("payment_status", sa.String(length=20), nullable=False, server_default="unpaid"))
    op.add_column("sales", sa.Column("issue_idempotency_key", sa.String(length=128), nullable=True))
    op.create_index("uq_sales_receipt_number", "sales", ["receipt_number"], unique=True)
    op.create_index("uq_sales_issue_idempotency_key", "sales", ["issue_idempotency_key"], unique=True)
    op.create_table("customers", sa.Column("id", sa.String(36), primary_key=True), sa.Column("name", sa.String(160), nullable=False), sa.Column("phone", sa.String(32), unique=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")))
    op.create_index("ix_customers_name", "customers", ["name"])
    op.create_table("receipt_series", sa.Column("id", sa.String(36), primary_key=True), sa.Column("series", sa.String(24), nullable=False), sa.Column("financial_year", sa.String(9), nullable=False), sa.Column("next_number", sa.Integer(), nullable=False), sa.UniqueConstraint("series", "financial_year", name="uq_receipt_series_year"))
    op.create_table("payments", sa.Column("id", sa.String(36), primary_key=True), sa.Column("sale_id", sa.String(36), nullable=False), sa.Column("customer_id", sa.String(36)), sa.Column("method", sa.String(20), nullable=False), sa.Column("amount", sa.Numeric(12, 2), nullable=False), sa.Column("reference", sa.String(120)), sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True), sa.Column("request_hash", sa.String(64), nullable=False), sa.Column("recorded_by_user_id", sa.String(36), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")), sa.ForeignKeyConstraint(["sale_id"], ["sales.id"]), sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]), sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"]))
    op.create_index("ix_payments_sale_id", "payments", ["sale_id"])
    op.create_table("customer_ledger_entries", sa.Column("id", sa.String(36), primary_key=True), sa.Column("customer_id", sa.String(36), nullable=False), sa.Column("direction", sa.String(10), nullable=False), sa.Column("amount", sa.Numeric(12, 2), nullable=False), sa.Column("entry_type", sa.String(32), nullable=False), sa.Column("reason", sa.String(300)), sa.Column("source_key", sa.String(160), nullable=False, unique=True), sa.Column("reversal_of_id", sa.String(36)), sa.Column("created_by_user_id", sa.String(36)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")), sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]), sa.ForeignKeyConstraint(["reversal_of_id"], ["customer_ledger_entries.id"]), sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]))
    op.create_index("ix_customer_ledger_entries_customer_id", "customer_ledger_entries", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_ledger_entries_customer_id", table_name="customer_ledger_entries")
    op.drop_table("customer_ledger_entries")
    op.drop_index("ix_payments_sale_id", table_name="payments")
    op.drop_table("payments")
    op.drop_table("receipt_series")
    op.drop_index("ix_customers_name", table_name="customers")
    op.drop_table("customers")
    op.drop_index("uq_sales_issue_idempotency_key", table_name="sales")
    op.drop_index("uq_sales_receipt_number", table_name="sales")
    op.drop_column("sales", "issue_idempotency_key")
    op.drop_column("sales", "payment_status")
    op.drop_column("sales", "receipt_number")
