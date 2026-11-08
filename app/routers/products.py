"""Protected product catalogue routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import get_session
from app.dependencies import require_roles
from app.models import Product, ProductBarcode, User, UserRole
from app.schemas import CheckoutSearchResponse, CheckoutSearchResult, ProductCreate, ProductList, ProductUpdate, PublicProduct

router = APIRouter(prefix="/api/products", tags=["products"])


def public_product(product: Product) -> PublicProduct:
    return PublicProduct(
        id=product.id,
        name=product.name,
        description=product.description,
        category=product.category,
        sku=product.sku,
        barcodes=[barcode.value for barcode in product.barcodes],
        unit_of_measure=product.unit_of_measure,
        pack_size=product.pack_size,
        selling_price=product.selling_price,
        mrp=product.mrp,
        tax_classification=product.tax_classification,
        tax_classification_status=product.tax_classification_status,
        is_active=product.is_active,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def product_or_404(product_id: str, session: Session) -> Product:
    product = session.scalar(select(Product).options(selectinload(Product.barcodes)).where(Product.id == product_id))
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")
    return product


def integrity_error_as_conflict(session: Session) -> None:
    session.rollback()
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Product SKU or barcode already exists.")


@router.post("", response_model=PublicProduct, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate,
    session: Session = Depends(get_session),
    _: User = Depends(require_roles(UserRole.ADMIN)),
) -> PublicProduct:
    product = Product(
        name=payload.name,
        description=payload.description,
        category=payload.category,
        sku=payload.sku,
        unit_of_measure=payload.unit_of_measure,
        pack_size=payload.pack_size,
        selling_price=payload.selling_price,
        mrp=payload.mrp,
        purchase_price=payload.purchase_price,
        tax_classification=payload.tax_classification,
        tax_classification_status=payload.tax_classification_status.value,
    )
    product.barcodes = [ProductBarcode(value=value) for value in payload.barcodes]
    session.add(product)
    try:
        session.commit()
    except IntegrityError:
        integrity_error_as_conflict(session)
    session.refresh(product)
    return public_product(product_or_404(product.id, session))


@router.get("", response_model=ProductList)
def list_products(
    search: str | None = Query(default=None, min_length=1, max_length=200),
    active: bool | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER, UserRole.VIEWER)),
) -> ProductList:
    query = select(Product).options(selectinload(Product.barcodes))
    count_query = select(func.count(Product.id))
    filters = []
    if active is not None:
        filters.append(Product.is_active.is_(active))
    if search is not None:
        pattern = f"%{search}%"
        barcode_ids = select(ProductBarcode.product_id).where(ProductBarcode.value.contains(search))
        filters.append(or_(Product.name.contains(search), Product.sku.contains(search), Product.id.in_(barcode_ids)))
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    products = session.scalars(query.order_by(Product.name, Product.id).offset(offset).limit(limit)).all()
    return ProductList(
        items=[public_product(product) for product in products],
        total=session.scalar(count_query) or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/search", response_model=CheckoutSearchResponse)
def checkout_search(
    query: str = Query(min_length=1, max_length=200),
    category: str | None = Query(default=None, min_length=1, max_length=80),
    session: Session = Depends(get_session),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER, UserRole.VIEWER)),
) -> CheckoutSearchResponse:
    lowered = query.lower()
    filters = [Product.is_active.is_(True)]
    if category is not None:
        filters.append(Product.category == category)
    filters.append(or_(
        Product.name.ilike(f"%{query}%"),
        Product.sku.ilike(f"%{query}%"),
        Product.barcodes.any(ProductBarcode.value.contains(query)),
    ))
    products = session.scalars(select(Product).options(selectinload(Product.barcodes)).where(*filters)).all()

    def match_type(product: Product) -> str:
        barcode_values = [barcode.value for barcode in product.barcodes]
        if query in barcode_values:
            return "exact_barcode"
        if product.sku == query:
            return "exact_sku"
        if any(query in barcode for barcode in barcode_values):
            return "partial_barcode"
        if product.sku and lowered in product.sku.lower():
            return "partial_sku"
        return "name"

    order = {"exact_barcode": 0, "exact_sku": 1, "partial_barcode": 2, "partial_sku": 3, "name": 4}
    results = []
    for product in sorted(products, key=lambda item: (order[match_type(item)], item.name.lower(), item.id)):
        result = public_product(product).model_dump()
        result["match_type"] = match_type(product)
        results.append(CheckoutSearchResult.model_validate(result))
    return CheckoutSearchResponse(items=results)


@router.get("/{product_id}", response_model=PublicProduct)
def get_product(
    product_id: str,
    session: Session = Depends(get_session),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.CASHIER, UserRole.VIEWER)),
) -> PublicProduct:
    return public_product(product_or_404(product_id, session))


@router.patch("/{product_id}", response_model=PublicProduct)
def update_product(
    product_id: str,
    payload: ProductUpdate,
    session: Session = Depends(get_session),
    _: User = Depends(require_roles(UserRole.ADMIN)),
) -> PublicProduct:
    product = product_or_404(product_id, session)
    changes = payload.model_dump(exclude_unset=True, exclude={"barcodes_to_add"})
    for field, value in changes.items():
        if field == "tax_classification_status" and value is not None:
            value = value.value
        setattr(product, field, value)
    product.barcodes.extend(ProductBarcode(value=value) for value in payload.barcodes_to_add)
    try:
        session.commit()
    except IntegrityError:
        integrity_error_as_conflict(session)
    return public_product(product_or_404(product.id, session))


@router.post("/{product_id}/deactivate", response_model=PublicProduct)
def deactivate_product(
    product_id: str,
    session: Session = Depends(get_session),
    _: User = Depends(require_roles(UserRole.ADMIN)),
) -> PublicProduct:
    product = product_or_404(product_id, session)
    product.is_active = False
    session.commit()
    return public_product(product_or_404(product.id, session))
