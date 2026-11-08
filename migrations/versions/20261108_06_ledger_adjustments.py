"""Add immutable adjustment references and one-time ledger reversals.

Revision ID: 20261108_06
Revises: 20261108_05
Create Date: 2026-11-08
"""

from alembic import op
import sqlalchemy as sa

revision = "20261108_06"
down_revision = "20261108_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch mode keeps this migration portable to SQLite, used for local POS work.
    with op.batch_alter_table("customer_ledger_entries") as batch:
        batch.add_column(sa.Column("reference_entry_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_customer_ledger_reference", "customer_ledger_entries", ["reference_entry_id"], ["id"])
        batch.create_unique_constraint("uq_customer_ledger_reversal", ["reversal_of_id"])


def downgrade() -> None:
    with op.batch_alter_table("customer_ledger_entries") as batch:
        batch.drop_constraint("uq_customer_ledger_reversal", type_="unique")
        batch.drop_constraint("fk_customer_ledger_reference", type_="foreignkey")
        batch.drop_column("reference_entry_id")
