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


def _get_own_product_or_403(db: Session, product_id: str, current_user: models.User) -> models.Product:
    """Récupère un produit en vérifiant qu'il appartient bien au vendeur connecté."""
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produit introuvable.")

    seller = db.query(models.Seller).filter(models.Seller.user_id == current_user.id).first()
    if not seller or product.seller_id != seller.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous ne pouvez modifier que vos propres produits.",
        )
    return product


@router.patch("/products/{product_id}", response_model=schemas.ProductOut)
def update_product(
    product_id: str,
    payload: schemas.ProductUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("seller")),
):
    product = _get_own_product_or_403(db, product_id, current_user)

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(product, field, value)

    db.commit()
    db.refresh(product)
    return product


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("seller")),
):
    """
    Suppression douce (soft delete) : le produit passe en is_active=False et
    disparaît du catalogue public, mais reste consultable via son ID direct --
    ce qui préserve l'intégrité des commandes passées qui le référencent
    (on ne casse jamais l'historique d'achat d'un client).
    """
    product = _get_own_product_or_403(db, product_id, current_user)
    product.is_active = False
    db.commit()
    return None
