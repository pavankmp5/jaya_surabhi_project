"""Payment recording, receipt configuration, and append-only khata ledger."""

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_session
from app.dependencies import require_roles
from app.models import Customer, CustomerLedgerEntry, LedgerDirection, Payment, PaymentMethod, PaymentStatus, ReceiptSeries, Sale, SaleStatus, User, UserRole
from app.schemas import CustomerCreate, CustomerRepayment, CustomerStatement, LedgerAdjustmentCreate, LedgerReversalCreate, PaymentCreate, PublicLedgerEntry, PublicPayment, ReceiptSeriesCreate
from app.schemas import PublicSale
from app.routers.sales import public_sale

router = APIRouter(tags=["financials"])
try:
    INDIA_TIMEZONE = ZoneInfo("Asia/Kolkata")
except Exception:  # Windows Python may lack the system IANA database.
    # India has no daylight-saving transition; this preserves its business date.
    INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")


def sale_total(sale: Sale) -> Decimal:
    return sum(((line.quantity * line.unit_price_snapshot).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for line in sale.lines), Decimal("0.00"))


def ledger_balance(customer_id: str, session: Session) -> Decimal:
    entries = session.scalars(select(CustomerLedgerEntry).where(CustomerLedgerEntry.customer_id == customer_id)).all()
    return sum((entry.amount if entry.direction == LedgerDirection.DEBIT.value else -entry.amount for entry in entries), Decimal("0.00"))


def entry_delta(entry: CustomerLedgerEntry) -> Decimal:
    return entry.amount if entry.direction == LedgerDirection.DEBIT.value else -entry.amount


def entry_public(entry: CustomerLedgerEntry, running_balance: Decimal | None = None) -> PublicLedgerEntry:
    return PublicLedgerEntry(id=entry.id, direction=entry.direction, amount=entry.amount, entry_type=entry.entry_type,
                             reason=entry.reason, created_at=entry.created_at, reference_entry_id=entry.reference_entry_id,
                             reversal_of_id=entry.reversal_of_id, running_balance=running_balance)


def india_business_date(value: datetime) -> date:
    """Interpret stored instants as UTC, then expose the local Asia/Kolkata calendar date."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(INDIA_TIMEZONE).date()


def payment_public(payment: Payment) -> PublicPayment:
    return PublicPayment(id=payment.id, method=payment.method, amount=payment.amount, customer_id=payment.customer_id, reference=payment.reference, created_at=payment.created_at)


@router.get("/api/customers")
def list_customers(session: Session = Depends(get_session), _: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER, UserRole.VIEWER))) -> list[dict[str, str | None]]:
    return [{"id": c.id, "name": c.name, "phone": c.phone} for c in session.scalars(select(Customer).order_by(Customer.name, Customer.id)).all()]


@router.get("/api/payment-history")
def all_payment_history(limit: int = 100, offset: int = 0, session: Session = Depends(get_session), _: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER, UserRole.VIEWER))) -> dict[str, object]:
    if limit < 1 or limit > 500 or offset < 0:
        raise HTTPException(status_code=422, detail="limit must be 1-500 and offset must not be negative.")
    query = select(Payment).order_by(Payment.created_at.desc(), Payment.id.desc())
    total = session.scalar(select(func.count()).select_from(Payment)) or 0
    rows = session.scalars(query.offset(offset).limit(limit)).all()
    sales = {sale.id: sale.receipt_number for sale in session.scalars(select(Sale).where(Sale.id.in_([p.sale_id for p in rows]))).all()} if rows else {}
    customer_ids = {p.customer_id for p in rows if p.customer_id}
    customers = {customer.id: customer.name for customer in session.scalars(select(Customer).where(Customer.id.in_(customer_ids))).all()} if customer_ids else {}
    return {"total": total, "limit": limit, "offset": offset, "payments": [
        {**payment_public(p).model_dump(mode="json"), "receipt_number": sales.get(p.sale_id),
         "customer_name": customers.get(p.customer_id) if p.customer_id else None}
        for p in rows]}


@router.post("/api/receipt-series", status_code=status.HTTP_201_CREATED)
def configure_receipt_series(payload: ReceiptSeriesCreate, session: Session = Depends(get_session), _: User = Depends(require_roles(UserRole.ADMIN))) -> dict[str, str]:
    record = ReceiptSeries(series=payload.series, financial_year=payload.financial_year, next_number=1)
    session.add(record)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Receipt series already exists for this financial year.")
    return {"series": record.series, "financial_year": record.financial_year}


@router.post("/api/customers", status_code=status.HTTP_201_CREATED)
def create_customer(payload: CustomerCreate, session: Session = Depends(get_session), user: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER))) -> dict[str, str]:
    customer = Customer(name=payload.name, phone=payload.phone)
    session.add(customer)
    session.flush()
    if payload.opening_balance is not None:
        session.add(CustomerLedgerEntry(customer_id=customer.id, direction=LedgerDirection.DEBIT.value, amount=payload.opening_balance, entry_type="opening_balance", reason="Explicit unverified opening balance", source_key=f"opening:{customer.id}", created_by_user_id=user.id))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Customer phone already exists.")
    return {"id": customer.id}


@router.get("/api/customers/{customer_id}/statement", response_model=CustomerStatement)
def customer_statement(customer_id: str, start_date: date | None = None, end_date: date | None = None,
                       limit: int = 100, offset: int = 0, session: Session = Depends(get_session),
                       _: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER, UserRole.VIEWER))) -> CustomerStatement:
    if limit < 1 or limit > 500 or offset < 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="limit must be 1-500 and offset must not be negative.")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="start_date must not be after end_date.")
    if session.get(Customer, customer_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found.")
    entries = session.scalars(select(CustomerLedgerEntry).where(CustomerLedgerEntry.customer_id == customer_id).order_by(CustomerLedgerEntry.created_at, CustomerLedgerEntry.id)).all()
    opening = Decimal("0.00")
    period: list[tuple[CustomerLedgerEntry, Decimal]] = []
    running = Decimal("0.00")
    for entry in entries:
        entry_date = india_business_date(entry.created_at)
        delta = entry_delta(entry)
        if start_date is not None and entry_date < start_date:
            opening += delta
            continue
        if end_date is not None and entry_date > end_date:
            continue
        running += delta
        period.append((entry, opening + running))
    page = period[offset:offset + limit]
    return CustomerStatement(customer_id=customer_id, outstanding_balance=ledger_balance(customer_id, session),
                             start_date=start_date, end_date=end_date, opening_balance=opening,
                             closing_balance=opening + running, limit=limit, offset=offset, total=len(period),
                             entries=[entry_public(entry, balance) for entry, balance in page])


@router.post("/api/customers/{customer_id}/repayments", response_model=PublicLedgerEntry, status_code=status.HTTP_201_CREATED)
def record_repayment(customer_id: str, payload: CustomerRepayment, idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128), session: Session = Depends(get_session), user: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER))) -> PublicLedgerEntry:
    if session.get(Customer, customer_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found.")
    source_key = f"repayment:{idempotency_key}"
    existing = session.scalar(select(CustomerLedgerEntry).where(CustomerLedgerEntry.source_key == source_key))
    if existing is not None:
        if existing.customer_id != customer_id or existing.amount != payload.amount:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency key was reused with different repayment data.")
        return entry_public(existing)
    entry = CustomerLedgerEntry(customer_id=customer_id, direction=LedgerDirection.CREDIT.value, amount=payload.amount, entry_type="repayment", reason=payload.reference, source_key=source_key, created_by_user_id=user.id)
    session.add(entry); session.commit()
    return entry_public(entry)


@router.post("/api/customers/{customer_id}/adjustments", response_model=PublicLedgerEntry, status_code=status.HTTP_201_CREATED)
def record_adjustment(customer_id: str, payload: LedgerAdjustmentCreate,
                      idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
                      session: Session = Depends(get_session), user: User = Depends(require_roles(UserRole.ADMIN))) -> PublicLedgerEntry:
    if session.get(Customer, customer_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found.")
    if payload.reference_entry_id:
        reference = session.get(CustomerLedgerEntry, payload.reference_entry_id)
        if reference is None or reference.customer_id != customer_id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Referenced ledger entry must belong to this customer.")
    source_key = f"adjustment:{idempotency_key}"
    existing = session.scalar(select(CustomerLedgerEntry).where(CustomerLedgerEntry.source_key == source_key))
    if existing is not None:
        if (existing.customer_id != customer_id or existing.amount != payload.amount or existing.direction != payload.direction
                or existing.reason != payload.reason or existing.reference_entry_id != payload.reference_entry_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency key was reused with different adjustment data.")
        return entry_public(existing)
    entry = CustomerLedgerEntry(customer_id=customer_id, direction=payload.direction, amount=payload.amount,
                                entry_type="adjustment", reason=payload.reason, source_key=source_key,
                                reference_entry_id=payload.reference_entry_id, created_by_user_id=user.id)
    session.add(entry)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Adjustment could not be recorded.")
    return entry_public(entry)


@router.post("/api/customers/{customer_id}/ledger/{entry_id}/reversals", response_model=PublicLedgerEntry, status_code=status.HTTP_201_CREATED)
def reverse_ledger_entry(customer_id: str, entry_id: str, payload: LedgerReversalCreate,
                         idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
                         session: Session = Depends(get_session), user: User = Depends(require_roles(UserRole.ADMIN))) -> PublicLedgerEntry:
    original = session.get(CustomerLedgerEntry, entry_id)
    if original is None or original.customer_id != customer_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found for this customer.")
    if original.entry_type == "reversal":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A reversal cannot itself be reversed.")
    source_key = f"reversal:{idempotency_key}"
    existing = session.scalar(select(CustomerLedgerEntry).where(CustomerLedgerEntry.source_key == source_key))
    if existing is not None:
        if existing.customer_id != customer_id or existing.reversal_of_id != original.id or existing.reason != payload.reason:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency key was reused with different reversal data.")
        return entry_public(existing)
    if session.scalar(select(CustomerLedgerEntry.id).where(CustomerLedgerEntry.reversal_of_id == original.id)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This ledger entry has already been reversed.")
    opposite = LedgerDirection.CREDIT.value if original.direction == LedgerDirection.DEBIT.value else LedgerDirection.DEBIT.value
    reversal = CustomerLedgerEntry(customer_id=customer_id, direction=opposite, amount=original.amount,
                                   entry_type="reversal", reason=payload.reason, source_key=source_key,
                                   reversal_of_id=original.id, reference_entry_id=original.id, created_by_user_id=user.id)
    session.add(reversal)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This ledger entry has already been reversed.")
    return entry_public(reversal)


@router.post("/api/sales/{sale_id}/payments", response_model=PublicPayment, status_code=status.HTTP_201_CREATED)
def record_payment(sale_id: str, payload: PaymentCreate, idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128), session: Session = Depends(get_session), user: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER))) -> PublicPayment:
    sale = session.get(Sale, sale_id)
    if sale is None or sale.status != SaleStatus.ISSUED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payments require an issued sale.")
    request_hash = hashlib.sha256(json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    existing = session.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.sale_id != sale_id or existing.request_hash != request_hash:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency key was reused with different payment data.")
        return payment_public(existing)
    if payload.method == PaymentMethod.KHATA.value:
        if not payload.customer_id or session.get(Customer, payload.customer_id) is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Khata payment requires an existing customer.")
    elif payload.customer_id is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Customer is only accepted for khata credit.")
    paid = sum(session.scalars(select(Payment.amount).where(Payment.sale_id == sale_id)).all(), Decimal("0.00"))
    if paid + payload.amount > sale_total(sale):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment exceeds the outstanding amount.")
    payment = Payment(sale_id=sale_id, customer_id=payload.customer_id, method=payload.method, amount=payload.amount, reference=payload.reference, idempotency_key=idempotency_key, request_hash=request_hash, recorded_by_user_id=user.id)
    session.add(payment)
    if payload.method == PaymentMethod.KHATA.value:
        session.add(CustomerLedgerEntry(customer_id=payload.customer_id, direction=LedgerDirection.DEBIT.value, amount=payload.amount, entry_type="credit_sale", reason=f"Receipt {sale.receipt_number}", source_key=f"payment:{idempotency_key}", created_by_user_id=user.id))
    new_paid = paid + payload.amount
    sale.payment_status = PaymentStatus.PAID.value if new_paid == sale_total(sale) else PaymentStatus.PARTIALLY_PAID.value
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment could not be recorded.")
    return payment_public(payment)


@router.get("/api/sales/{sale_id}/payments", response_model=list[PublicPayment])
def payment_history(sale_id: str, session: Session = Depends(get_session), _: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER, UserRole.VIEWER))) -> list[PublicPayment]:
    return [payment_public(payment) for payment in session.scalars(select(Payment).where(Payment.sale_id == sale_id).order_by(Payment.created_at, Payment.id)).all()]


@router.get("/api/receipts/{receipt_number:path}")
def receipt_by_number(receipt_number: str, session: Session = Depends(get_session), _: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER, UserRole.VIEWER))) -> dict[str, object]:
    sale = session.scalar(select(Sale).where(Sale.receipt_number == receipt_number))
    if sale is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found.")
    sale = session.scalar(select(Sale).options(selectinload(Sale.lines)).where(Sale.id == sale.id))
    total = sale_total(sale)
    payments = session.scalars(select(Payment).where(Payment.sale_id == sale.id).order_by(Payment.created_at, Payment.id)).all()
    received = sum((p.amount for p in payments if p.method != PaymentMethod.KHATA.value), Decimal("0.00"))
    khata = sum((p.amount for p in payments if p.method == PaymentMethod.KHATA.value), Decimal("0.00"))
    settled = received + khata
    presentation = public_sale(sale).model_dump(mode="json")
    for line in presentation["lines"]:
        line["line_total"] = str((Decimal(line["quantity"]) * Decimal(line["unit_price"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return {**presentation, "total": str(total), "received_total": str(received), "khata_total": str(khata),
            "settled_total": str(settled), "balance_due": str(total-settled),
            "payments": [payment_public(p).model_dump(mode="json") for p in payments]}
