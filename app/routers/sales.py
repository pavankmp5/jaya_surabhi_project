"""Counter-sale drafts with immutable product and temporary-item snapshots."""

from datetime import UTC, datetime

from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_session
from app.dependencies import require_roles
from app.models import Payment, Product, ReceiptSeries, Sale, SaleLine, SaleStatus, User, UserRole
from app.schemas import CatalogueSaleLineCreate, IssueSaleRequest, PublicSale, PublicSaleLine, TemporarySaleLineCreate

router = APIRouter(prefix="/api/sales", tags=["sales"])


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def sale_or_404(sale_id: str, session: Session) -> Sale:
    sale = session.scalar(select(Sale).options(selectinload(Sale.lines)).where(Sale.id == sale_id))
    if sale is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sale not found.")
    return sale


def editable_sale(sale_id: str, session: Session) -> Sale:
    sale = sale_or_404(sale_id, session)
    if sale.status != SaleStatus.DRAFT.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Issued sales cannot be changed.")
    return sale


def public_sale(sale: Sale) -> PublicSale:
    return PublicSale(
        id=sale.id, status=sale.status, created_at=sale.created_at, issued_at=sale.issued_at,
        receipt_number=sale.receipt_number, payment_status=sale.payment_status,
        lines=[PublicSaleLine(
            id=line.id, line_number=line.line_number, product_id=line.product_id, is_temporary=line.is_temporary,
            description=line.description_snapshot, sku=line.sku_snapshot, unit=line.unit_snapshot,
            quantity=line.quantity, mrp=line.mrp_snapshot, unit_price=line.unit_price_snapshot,
            tax_classification=line.tax_classification_snapshot,
            tax_classification_status=line.tax_classification_status_snapshot,
        ) for line in sale.lines],
    )


@router.post("", response_model=PublicSale, status_code=status.HTTP_201_CREATED)
def create_sale(
    session: Session = Depends(get_session),
    user: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER)),
) -> PublicSale:
    sale = Sale(created_by_user_id=user.id)
    session.add(sale)
    session.commit()
    return public_sale(sale_or_404(sale.id, session))


@router.get("/{sale_id}", response_model=PublicSale)
def get_sale(
    sale_id: str,
    session: Session = Depends(get_session),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER)),
) -> PublicSale:
    return public_sale(sale_or_404(sale_id, session))


@router.post("/{sale_id}/lines/catalogue", response_model=PublicSale, status_code=status.HTTP_201_CREATED)
def add_catalogue_line(
    sale_id: str,
    payload: CatalogueSaleLineCreate,
    session: Session = Depends(get_session),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER)),
) -> PublicSale:
    sale = editable_sale(sale_id, session)
    product = session.get(Product, payload.product_id)
    if product is None or not product.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active product not found.")
    sale.lines.append(SaleLine(
        product_id=product.id, line_number=len(sale.lines) + 1, is_temporary=False,
        description_snapshot=product.name, sku_snapshot=product.sku, unit_snapshot=product.unit_of_measure,
        quantity=payload.quantity, mrp_snapshot=product.mrp, unit_price_snapshot=product.selling_price,
        tax_classification_snapshot=product.tax_classification,
        tax_classification_status_snapshot=product.tax_classification_status,
    ))
    session.commit()
    return public_sale(sale_or_404(sale.id, session))


@router.post("/{sale_id}/lines/temporary", response_model=PublicSale, status_code=status.HTTP_201_CREATED)
def add_temporary_line(
    sale_id: str,
    payload: TemporarySaleLineCreate,
    session: Session = Depends(get_session),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER)),
) -> PublicSale:
    sale = editable_sale(sale_id, session)
    sale.lines.append(SaleLine(
        product_id=None, line_number=len(sale.lines) + 1, is_temporary=True,
        description_snapshot=payload.description, sku_snapshot=None, unit_snapshot=payload.unit,
        quantity=payload.quantity, mrp_snapshot=payload.mrp, unit_price_snapshot=payload.price,
        tax_classification_snapshot=payload.tax_classification,
        tax_classification_status_snapshot=payload.tax_classification_status.value,
    ))
    session.commit()
    return public_sale(sale_or_404(sale.id, session))


def _financial_year(now: datetime) -> str:
    start = now.year if now.month >= 4 else now.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


@router.post("/{sale_id}/issue", response_model=PublicSale)
def issue_sale(
    sale_id: str,
    payload: IssueSaleRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    session: Session = Depends(get_session),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER)),
) -> PublicSale:
    sale = sale_or_404(sale_id, session)
    if sale.status == SaleStatus.ISSUED.value:
        if sale.issue_idempotency_key == idempotency_key:
            return public_sale(sale)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sale is already issued.")
    if not sale.lines:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A sale requires at least one line.")
    issued_at = _utcnow()
    receipt_series = session.scalar(select(ReceiptSeries).where(
        ReceiptSeries.series == payload.series, ReceiptSeries.financial_year == _financial_year(issued_at)
    ))
    if receipt_series is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No receipt series is configured for this financial year.")
    number = receipt_series.next_number
    receipt_series.next_number += 1
    sale.status, sale.issued_at, sale.issue_idempotency_key = SaleStatus.ISSUED.value, issued_at, idempotency_key
    sale.receipt_number = f"{receipt_series.series}/{receipt_series.financial_year}/{number:06d}"
    session.commit()
    return public_sale(sale_or_404(sale.id, session))
