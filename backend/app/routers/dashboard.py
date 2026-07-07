"""
Endpoints du dashboard vendeur.

GET /dashboard/summary   -> statistiques agrégées (CA, commandes, stock faible, courbe 30j)
GET /dashboard/products  -> produits du vendeur connecté avec leur stock courant
POST /dashboard/products -> création de produit (réutilise la logique de products.py)
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from app.database import get_db
from app import models, schemas
from app.dependencies import require_role

router = APIRouter(prefix="/dashboard", tags=["dashboard vendeur"])


def _get_seller_or_404(db: Session, user: models.User) -> models.Seller:
    seller = db.query(models.Seller).filter(models.Seller.user_id == user.id).first()
    if not seller:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucun profil vendeur associé à ce compte.",
        )
    return seller


@router.get("/products", response_model=list[schemas.SellerProductOut])
def my_products(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("seller")),
):
    seller = _get_seller_or_404(db, current_user)
    return (
        db.query(models.Product)
        .options(joinedload(models.Product.category), joinedload(models.Product.stock))
        .filter(models.Product.seller_id == seller.id)
        .order_by(models.Product.created_at.desc())
        .all()
    )


@router.get("/summary", response_model=schemas.SellerDashboardSummary)
def dashboard_summary(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("seller")),
):
    seller = _get_seller_or_404(db, current_user)

    product_ids_subq = (
        db.query(models.Product.id).filter(models.Product.seller_id == seller.id).subquery()
    )

    total_products = db.query(models.Product).filter(models.Product.seller_id == seller.id).count()

    low_stock_count = (
        db.query(models.Stock)
        .join(models.Product, models.Stock.product_id == models.Product.id)
        .filter(
            models.Product.seller_id == seller.id,
            models.Stock.quantity <= models.Stock.low_stock_threshold,
        )
        .count()
    )

    revenue_orders_query = (
        db.query(
            models.OrderItem.order_id,
            (models.OrderItem.quantity * models.OrderItem.unit_price).label("line_total"),
            models.Order.created_at,
        )
        .join(models.Order, models.OrderItem.order_id == models.Order.id)
        .filter(models.OrderItem.product_id.in_(product_ids_subq))
        .filter(models.Order.status != models.OrderStatus.CANCELLED)
    )
    rows = revenue_orders_query.all()

    total_revenue = sum(r.line_total for r in rows)
    total_orders = len({r.order_id for r in rows})

    # Courbe de revenu sur les 30 derniers jours (pour le graphique du dashboard).
    since = datetime.utcnow() - timedelta(days=30)
    revenue_by_day: dict[str, dict] = {}
    for r in rows:
        if r.created_at < since:
            continue
        day_key = r.created_at.strftime("%Y-%m-%d")
        if day_key not in revenue_by_day:
            revenue_by_day[day_key] = {"revenue": 0.0, "orders": set()}
        revenue_by_day[day_key]["revenue"] += r.line_total
        revenue_by_day[day_key]["orders"].add(r.order_id)

    revenue_last_30_days = [
        schemas.RevenuePoint(date=day, revenue=round(data["revenue"], 2), orders_count=len(data["orders"]))
        for day, data in sorted(revenue_by_day.items())
    ]

    recent_movements = (
        db.query(models.StockMovement)
        .filter(models.StockMovement.product_id.in_(product_ids_subq))
        .order_by(models.StockMovement.created_at.desc())
        .limit(20)
        .all()
    )

    return schemas.SellerDashboardSummary(
        total_products=total_products,
        total_revenue=round(total_revenue, 2),
        total_orders=total_orders,
        low_stock_count=low_stock_count,
        revenue_last_30_days=revenue_last_30_days,
        recent_movements=recent_movements,
    )
