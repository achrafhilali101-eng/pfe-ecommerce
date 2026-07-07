"""
Endpoints du catalogue produits.

GET  /products              -> liste paginée, avec recherche/filtre par catégorie
GET  /products/{id}         -> détail d'un produit
GET  /categories            -> liste des catégories (pour les filtres frontend)
POST /products              -> création (réservé aux vendeurs)
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_

from app.database import get_db
from app import models, schemas
from app.dependencies import require_role

router = APIRouter(tags=["catalogue"])


@router.get("/categories", response_model=list[schemas.CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    return db.query(models.Category).order_by(models.Category.name).all()


@router.get("/products", response_model=schemas.PaginatedProducts)
def list_products(
    db: Session = Depends(get_db),
    search: Optional[str] = Query(default=None, description="Recherche sur le nom du produit"),
    category_id: Optional[str] = Query(default=None),
    min_price: Optional[float] = Query(default=None, ge=0),
    max_price: Optional[float] = Query(default=None, ge=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = db.query(models.Product).filter(models.Product.is_active.is_(True))

    if search:
        query = query.filter(models.Product.name.ilike(f"%{search}%"))
    if category_id:
        query = query.filter(models.Product.category_id == category_id)
    if min_price is not None:
        query = query.filter(models.Product.price >= min_price)
    if max_price is not None:
        query = query.filter(models.Product.price <= max_price)

    total = query.count()
    items = (
        query.options(joinedload(models.Product.category))
        .order_by(models.Product.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return schemas.PaginatedProducts(total=total, page=page, page_size=page_size, items=items)


@router.get("/products/{product_id}", response_model=schemas.ProductOut)
def get_product(product_id: str, db: Session = Depends(get_db)):
    product = (
        db.query(models.Product)
        .options(
            joinedload(models.Product.category),
            joinedload(models.Product.seller),
            joinedload(models.Product.stock),
        )
        .filter(models.Product.id == product_id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produit introuvable.")

    return product


@router.post("/products", response_model=schemas.ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: schemas.ProductCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("seller")),
):
    seller = db.query(models.Seller).filter(models.Seller.user_id == current_user.id).first()
    if not seller:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucun profil vendeur associé à ce compte.",
        )

    product = models.Product(
        seller_id=seller.id,
        category_id=payload.category_id,
        name=payload.name,
        description=payload.description,
        price=payload.price,
        image_url=payload.image_url,
    )
    db.add(product)
    db.flush()

    stock = models.Stock(product_id=product.id, quantity=payload.initial_stock)
    db.add(stock)

    db.commit()
    db.refresh(product)
    return product
