"""
Endpoints de commandes.

POST /orders          -> crée une commande (plusieurs lignes), décrémente le stock
                          de façon atomique (tout ou rien), trace une interaction
                          d'achat pour chaque produit (alimente le moteur de reco).
GET  /orders          -> historique des commandes de l'utilisateur courant
GET  /orders/{id}     -> détail d'une commande
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app import models, schemas
from app.dependencies import get_current_user
from app.routers.stocks import decrement_stock_for_sale

router = APIRouter(prefix="/orders", tags=["commandes"])


@router.post("", response_model=schemas.OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(
    payload: schemas.OrderCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not payload.items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La commande est vide.")

    order = models.Order(
        buyer_id=current_user.id,
        status=models.OrderStatus.PENDING,
        total_amount=0.0,
        shipping_address=payload.shipping_address,
        shipping_phone=payload.shipping_phone,
    )
    db.add(order)
    db.flush()

    total = 0.0
    try:
        for item in payload.items:
            product = db.query(models.Product).filter(models.Product.id == item.product_id).first()
            if not product or not product.is_active:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Produit introuvable : {item.product_id}",
                )

            # Décrément atomique du stock (lève une HTTPException si insuffisant) --
            # fait partie de la même transaction que la création de la commande :
            # si une ligne échoue, TOUT est annulé (pas de commande à moitié créée).
            decrement_stock_for_sale(db, item.product_id, item.quantity)

            order_item = models.OrderItem(
                order_id=order.id,
                product_id=product.id,
                quantity=item.quantity,
                unit_price=product.price,
            )
            db.add(order_item)
            total += product.price * item.quantity

            # Trace un signal d'achat pour le moteur de recommandation.
            interaction = models.Interaction(
                user_id=current_user.id,
                product_id=product.id,
                interaction_type=models.InteractionType.PURCHASE,
                weight=5.0,
            )
            db.add(interaction)

        order.total_amount = total
        order.status = models.OrderStatus.PAID  # simplifié : pas d'intégration Stripe réelle ici
        db.commit()

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Échec de la création de la commande, aucune modification appliquée.",
        )

    db.refresh(order)
    return order


@router.get("", response_model=list[schemas.OrderOut])
def list_my_orders(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return (
        db.query(models.Order)
        .options(joinedload(models.Order.items).joinedload(models.OrderItem.product))
        .filter(models.Order.buyer_id == current_user.id)
        .order_by(models.Order.created_at.desc())
        .all()
    )


@router.get("/{order_id}", response_model=schemas.OrderOut)
def get_order(
    order_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    order = (
        db.query(models.Order)
        .options(joinedload(models.Order.items).joinedload(models.OrderItem.product))
        .filter(models.Order.id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Commande introuvable.")
    if order.buyer_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Accès non autorisé.")

    return order
