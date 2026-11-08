"""Persisted application models."""

import uuid
from datetime import datetime
from enum import StrEnum

from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserRole(StrEnum):
    ADMIN = "admin"
    CASHIER = "cashier"
    VIEWER = "viewer"


class TaxClassificationStatus(StrEnum):
    UNKNOWN = "unknown"
    UNVERIFIED = "unverified"
    VERIFIED = "verified"


class ImportPreviewStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class SaleStatus(StrEnum):
    DRAFT = "draft"
    ISSUED = "issued"


class PaymentMethod(StrEnum):
    CASH = "cash"
    UPI = "upi"
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"
    KHATA = "khata"


class PaymentStatus(StrEnum):
    UNPAID = "unpaid"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"


class LedgerDirection(StrEnum):
    DEBIT = "debit"  # Increases the amount the customer owes.
    CREDIT = "credit"  # Decreases the amount the customer owes.


class User(Base):
    """An application user with one server-enforced role."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=UserRole.CASHIER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Product(Base):
    """Catalogue data; it deliberately contains no stock or purchase workflow state."""

    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    sku: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True, index=True)
    unit_of_measure: Mapped[str] = mapped_column(String(32), nullable=False)
    pack_size: Mapped[str | None] = mapped_column(String(80), nullable=True)
    selling_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    mrp: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    tax_classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tax_classification_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaxClassificationStatus.UNKNOWN.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    barcodes: Mapped[list["ProductBarcode"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="ProductBarcode.value"
    )


class ProductBarcode(Base):
    """A supplied barcode identifier, stored exactly as received."""

    __tablename__ = "product_barcodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    value: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    product: Mapped[Product] = relationship(back_populates="barcodes")


class ProductImportPreviewRecord(Base):
    """Temporary canonical import data plus a durable, file-free audit summary."""

    __tablename__ = "product_import_previews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    confirmation_secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_rows: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ImportPreviewStatus.PENDING.value, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    confirmed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Sale(Base):
    """A draft or issued counter bill; money and tax data live on immutable line snapshots."""

    __tablename__ = "sales"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=SaleStatus.DRAFT.value, index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    receipt_number: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)
    payment_status: Mapped[str] = mapped_column(String(20), nullable=False, default=PaymentStatus.UNPAID.value)
    issue_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    lines: Mapped[list["SaleLine"]] = relationship(back_populates="sale", cascade="all, delete-orphan", order_by="SaleLine.line_number")


class SaleLine(Base):
    """A sale-time product snapshot; temporary lines never create Product records."""

    __tablename__ = "sale_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id", ondelete="RESTRICT"), nullable=False, index=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=True, index=True)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    is_temporary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    sku_snapshot: Mapped[str | None] = mapped_column(String(80), nullable=True)
    unit_snapshot: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    mrp_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    unit_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    tax_classification_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tax_classification_status_snapshot: Mapped[str] = mapped_column(String(20), nullable=False)

    sale: Mapped[Sale] = relationship(back_populates="lines")


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ReceiptSeries(Base):
    __tablename__ = "receipt_series"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    series: Mapped[str] = mapped_column(String(24), nullable=False)
    financial_year: Mapped[str] = mapped_column(String(9), nullable=False)
    next_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __table_args__ = ()


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id", ondelete="RESTRICT"), nullable=False, index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), nullable=True)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CustomerLedgerEntry(Base):
    __tablename__ = "customer_ledger_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    reversal_of_id: Mapped[str | None] = mapped_column(ForeignKey("customer_ledger_entries.id", ondelete="RESTRICT"), nullable=True)
    reference_entry_id: Mapped[str | None] = mapped_column(ForeignKey("customer_ledger_entries.id", ondelete="RESTRICT"), nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # A reversal is a one-time compensating entry.  Adjustments can only reference,
    # never replace, a prior entry.
    __table_args__ = (UniqueConstraint("reversal_of_id", name="uq_customer_ledger_reversal"),)
