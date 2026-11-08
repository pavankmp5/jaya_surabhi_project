"""Validated API request and response schemas."""

from decimal import Decimal
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal

from app.models import TaxClassificationStatus, UserRole


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)


class CreateUser(Credentials):
    role: UserRole = UserRole.CASHIER


class PublicUser(BaseModel):
    id: str
    username: str
    role: UserRole


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ProductInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    sku: str | None = Field(default=None, min_length=1, max_length=80)
    barcodes: list[str] = Field(default_factory=list, max_length=50)
    unit_of_measure: str = Field(min_length=1, max_length=32)
    pack_size: str | None = Field(default=None, min_length=1, max_length=80)
    selling_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    mrp: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    purchase_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    tax_classification: str | None = Field(default=None, min_length=1, max_length=64)
    tax_classification_status: TaxClassificationStatus = TaxClassificationStatus.UNKNOWN

    @field_validator("barcodes")
    @classmethod
    def validate_barcodes(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("Barcode values must be unique within a product.")
        for value in values:
            if not value or len(value) > 128 or value != value.strip() or any(character.isspace() for character in value):
                raise ValueError("Each barcode must be 1-128 non-whitespace characters and is stored unchanged.")
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError("Barcode values cannot contain control characters.")
        return values


class ProductCreate(ProductInput):
    pass


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    sku: str | None = Field(default=None, min_length=1, max_length=80)
    unit_of_measure: str | None = Field(default=None, min_length=1, max_length=32)
    pack_size: str | None = Field(default=None, min_length=1, max_length=80)
    selling_price: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    mrp: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    purchase_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    tax_classification: str | None = Field(default=None, min_length=1, max_length=64)
    tax_classification_status: TaxClassificationStatus | None = None
    barcodes_to_add: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("barcodes_to_add")
    @classmethod
    def validate_new_barcodes(cls, values: list[str]) -> list[str]:
        return ProductInput.validate_barcodes(values)


class PublicProduct(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    category: str | None
    sku: str | None
    barcodes: list[str]
    unit_of_measure: str
    pack_size: str | None
    selling_price: Decimal
    mrp: Decimal | None
    tax_classification: str | None
    tax_classification_status: TaxClassificationStatus
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProductList(BaseModel):
    items: list[PublicProduct]
    total: int
    limit: int
    offset: int


class CheckoutSearchResult(PublicProduct):
    match_type: Literal["exact_barcode", "exact_sku", "partial_barcode", "partial_sku", "name"]


class CheckoutSearchResponse(BaseModel):
    items: list[CheckoutSearchResult]


class CreateSale(BaseModel):
    pass


class CatalogueSaleLineCreate(BaseModel):
    product_id: str = Field(min_length=36, max_length=36)
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)


class TemporarySaleLineCreate(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    unit: str = Field(min_length=1, max_length=32)
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    mrp: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    tax_classification: str | None = Field(default=None, min_length=1, max_length=64)
    tax_classification_status: TaxClassificationStatus


class PublicSaleLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    line_number: int
    product_id: str | None
    is_temporary: bool
    description: str
    sku: str | None
    unit: str
    quantity: Decimal
    mrp: Decimal | None
    unit_price: Decimal
    tax_classification: str | None
    tax_classification_status: TaxClassificationStatus


class PublicSale(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    created_at: datetime
    issued_at: datetime | None
    receipt_number: str | None = None
    payment_status: str
    lines: list[PublicSaleLine]


class ReceiptSeriesCreate(BaseModel):
    series: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9/-]+$")
    financial_year: str = Field(pattern=r"^\d{4}-\d{2}$")


class IssueSaleRequest(BaseModel):
    series: str = Field(min_length=1, max_length=24)


class PaymentCreate(BaseModel):
    method: Literal["cash", "upi", "card", "bank_transfer", "khata"]
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    customer_id: str | None = None
    reference: str | None = Field(default=None, max_length=120)


class PublicPayment(BaseModel):
    id: str
    method: str
    amount: Decimal
    customer_id: str | None
    reference: str | None
    created_at: datetime


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    phone: str | None = Field(default=None, min_length=3, max_length=32)
    opening_balance: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)


class CustomerRepayment(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reference: str | None = Field(default=None, max_length=120)


class LedgerAdjustmentCreate(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    direction: Literal["debit", "credit"]
    reason: str = Field(min_length=8, max_length=300)
    reference_entry_id: str | None = Field(default=None, min_length=36, max_length=36)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("An adjustment reason is required.")
        return value.strip()


class LedgerReversalCreate(BaseModel):
    reason: str = Field(min_length=8, max_length=300)

    @field_validator("reason")
    @classmethod
    def meaningful_reversal_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reversal reason is required.")
        return value.strip()


class PublicLedgerEntry(BaseModel):
    id: str
    direction: str
    amount: Decimal
    entry_type: str
    reason: str | None
    created_at: datetime
    reference_entry_id: str | None = None
    reversal_of_id: str | None = None
    running_balance: Decimal | None = None


class CustomerStatement(BaseModel):
    customer_id: str
    outstanding_balance: Decimal
    start_date: date | None = None
    end_date: date | None = None
    opening_balance: Decimal = Decimal("0.00")
    closing_balance: Decimal = Decimal("0.00")
    limit: int = 100
    offset: int = 0
    total: int = 0
    entries: list[PublicLedgerEntry]


class ImportRowError(BaseModel):
    row_number: int
    field: str | None = None
    message: str
    kind: Literal["validation", "conflict"] = "validation"


class ImportProductProposal(BaseModel):
    row_number: int
    name: str
    sku: str | None
    barcodes: list[str]
    unit_of_measure: str
    pack_size: str | None
    selling_price: Decimal
    tax_classification: str | None
    tax_classification_status: TaxClassificationStatus


class ProductImportPreview(BaseModel):
    total_rows: int
    valid_rows: int
    rejected_rows: int
    conflicting_rows: int
    proposed_additions: list[ImportProductProposal]
    errors: list[ImportRowError]
    confirmation_token: str | None = None


class ConfirmProductImport(BaseModel):
    confirmation_token: str = Field(min_length=70, max_length=200)


class ProductImportResult(BaseModel):
    created_count: int
    rejected_count: int
    conflicting_count: int
