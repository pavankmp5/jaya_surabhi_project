import os
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_catalogue_migration_upgrades_and_downgrades_clean_database(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        database_path = Path(temporary_directory) / "catalogue-test.db"
        monkeypatch.setenv("POS_DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
        config = Config("alembic.ini")

        command.upgrade(config, "head")
        migration_engine = create_engine(os.environ["POS_DATABASE_URL"])
        try:
            assert {"users", "products", "product_barcodes", "product_import_previews", "sales", "sale_lines", "customers", "payments", "receipt_series", "customer_ledger_entries", "alembic_version"} <= set(
                inspect(migration_engine).get_table_names()
            )

            command.downgrade(config, "20261025_01")
            assert "products" not in inspect(migration_engine).get_table_names()
            assert "product_barcodes" not in inspect(migration_engine).get_table_names()
            assert "product_import_previews" not in inspect(migration_engine).get_table_names()
            assert "sales" not in inspect(migration_engine).get_table_names()
            assert "sale_lines" not in inspect(migration_engine).get_table_names()
        finally:
            migration_engine.dispose()
