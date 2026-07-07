"""
Endpoints de gestion des stocks.

GET  /stocks/{product_id}          -> niveau de stock courant
POST /stocks/{product_id}/adjust   -> ajustement manuel (restock, correction) — réservé au vendeur
                                       propriétaire du produit. Écrit en transaction atomique
                                       + trace un StockMovement pour l'audit et le dashboard.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas
from app.dependencies import require_role

router = APIRouter(prefix="/stocks", tags=["stocks"])


def _get_product_or_404(db: Session, product_id: str) -> models.Product:
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produit introuvable.")
    return product


@router.get("/{product_id}", response_model=schemas.StockOut)
def get_stock(product_id: str, db: Session = Depends(get_db)):
    product = _get_product_or_404(db, product_id)
    if not product.stock:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aucun stock associé.")
    return product.stock


@router.post("/{product_id}/adjust", response_model=schemas.StockOut)
def adjust_stock(
    product_id: str,
    payload: schemas.StockAdjust,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role("seller")),
):
    product = _get_product_or_404(db, product_id)

    seller = db.query(models.Seller).filter(models.Seller.user_id == current_user.id).first()
    if not seller or product.seller_id != seller.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous ne pouvez ajuster que le stock de vos propres produits.",
        )

    if not product.stock:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aucun stock associé.")

    new_quantity = product.stock.quantity + payload.quantity_delta
    if new_quantity < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stock insuffisant : {product.stock.quantity} disponible(s), "
                   f"ajustement de {payload.quantity_delta} demandé.",
        )

    # Transaction atomique : on met à jour le stock ET on trace le mouvement ensemble,
    # ou aucun des deux (évite toute désynchronisation entre stock courant et historique).
    try:
        product.stock.quantity = new_quantity

        movement_type = (
            models.StockMovementType.RESTOCK if payload.quantity_delta > 0
            else models.StockMovementType.ADJUSTMENT
        )
        movement = models.StockMovement(
            product_id=product.id,
            movement_type=movement_type,
            quantity_delta=payload.quantity_delta,
            reason=payload.reason or "Ajustement manuel vendeur",
        )
        db.add(movement)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Échec de la mise à jour du stock, aucune modification appliquée.",
        )

    db.refresh(product.stock)
    return product.stock


def decrement_stock_for_sale(db: Session, product_id: str, quantity: int) -> None:
    """
    Fonction utilitaire réutilisée par le module de commandes (Jour 8) pour décrémenter
    le stock de façon atomique au moment de l'achat. Lève une exception si stock insuffisant.
    """
    stock = db.query(models.Stock).filter(models.Stock.product_id == product_id).with_for_update().first()
    if not stock or stock.quantity < quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stock insuffisant pour cette commande.",
        )

    stock.quantity -= quantity
    movement = models.StockMovement(
        product_id=product_id,
        movement_type=models.StockMovementType.SALE,
        quantity_delta=-quantity,
        reason="Vente",
    )
    db.add(movement)
    # NB: le commit est laissé à l'appelant pour que ça fasse partie de la transaction
    # globale de création de commande (atomicité commande + stock).
