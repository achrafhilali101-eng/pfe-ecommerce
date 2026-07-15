"""
Endpoints de commandes.

POST /orders                    -> crée une commande directement payée (utilisé par les tests /
                                    scénarios sans paiement réel)
POST /orders/checkout-session    -> crée une session Stripe Checkout (sandbox) pour un vrai paiement
GET  /orders/{id}/confirm-payment -> à appeler après retour de Stripe : vérifie le paiement,
                                     finalise la commande (décrément stock, interactions, etc.)
GET  /orders          -> historique des commandes de l'utilisateur courant
GET  /orders/{id}     -> détail d'une commande
"""

import stripe
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app import models, schemas
from app.dependencies import get_current_user
from app.routers.stocks import decrement_stock_for_sale
from app.ws_manager import manager

stripe.api_key = settings.STRIPE_SECRET_KEY

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
        shipping_email=current_user.email,
    )
    db.add(order)
    db.flush()

    total = 0.0
    updated_stock_quantities = {}  # product_id -> nouvelle quantité (diffusé après commit uniquement)
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
            new_quantity = decrement_stock_for_sale(db, item.product_id, item.quantity)
            updated_stock_quantities[product.id] = new_quantity

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

    # Diffusion temps réel -- uniquement après un commit réussi, jamais avant :
    # on ne veut jamais annoncer un changement de stock qui pourrait être annulé.
    for product_id, new_quantity in updated_stock_quantities.items():
        manager.broadcast_from_sync({
            "type": "stock_update",
            "product_id": product_id,
            "quantity": new_quantity,
        })

    db.refresh(order)
    return order


@router.post("/checkout-session", response_model=schemas.CheckoutSessionOut, status_code=status.HTTP_201_CREATED)
def create_checkout_session(
    payload: schemas.OrderCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Crée une commande en attente (stock PAS encore décrémenté) puis une session
    Stripe Checkout (sandbox) pointant vers cette commande. Le stock n'est
    décrémenté qu'à la confirmation du paiement (voir /confirm-payment),
    pour ne jamais bloquer du stock sur un paiement abandonné.
    """
    if not payload.items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La commande est vide.")

    line_items = []
    products_by_id = {}
    total = 0.0

    for item in payload.items:
        product = db.query(models.Product).filter(models.Product.id == item.product_id).first()
        if not product or not product.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Produit introuvable : {item.product_id}",
            )
        stock = db.query(models.Stock).filter(models.Stock.product_id == product.id).first()
        if not stock or stock.quantity < item.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Stock insuffisant pour : {product.name}",
            )
        products_by_id[product.id] = product
        total += product.price * item.quantity
        line_items.append({
            "price_data": {
                "currency": "usd",
                "product_data": {"name": product.name},
                "unit_amount": round(product.price * 100),  # Stripe attend des centimes
            },
            "quantity": item.quantity,
        })

    order = models.Order(
        buyer_id=current_user.id,
        status=models.OrderStatus.PENDING,
        total_amount=total,
        shipping_address=payload.shipping_address,
        shipping_phone=payload.shipping_phone,
        shipping_email=current_user.email,
    )
    db.add(order)
    db.flush()

    for item in payload.items:
        product = products_by_id[item.product_id]
        db.add(models.OrderItem(
            order_id=order.id,
            product_id=product.id,
            quantity=item.quantity,
            unit_price=product.price,
        ))

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=line_items,
            success_url=f"{settings.FRONTEND_URL}/confirmation?session_id={{CHECKOUT_SESSION_ID}}&order_id={order.id}",
            cancel_url=f"{settings.FRONTEND_URL}/panier",
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Impossible de contacter Stripe : {exc}",
        )

    order.stripe_payment_intent_id = session.id
    db.commit()

    return schemas.CheckoutSessionOut(checkout_url=session.url, order_id=order.id)


@router.get("/{order_id}/confirm-payment", response_model=schemas.OrderOut)
def confirm_payment(
    order_id: str,
    session_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Appelé par le frontend juste après le retour de Stripe (success_url).
    Vérifie que le paiement est bien confirmé côté Stripe, puis finalise
    réellement la commande (décrément de stock, traçage pour la reco).
    Idempotent : si déjà confirmée, retourne simplement la commande existante.
    """
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

    if order.status == models.OrderStatus.PAID:
        return order  # déjà confirmée (ex: utilisateur qui rafraîchit la page)

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Impossible de vérifier le paiement auprès de Stripe : {exc}",
        )

    if session.payment_status != "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le paiement n'a pas été confirmé par Stripe.",
        )

    updated_stock_quantities = {}
    try:
        for order_item in order.items:
            new_quantity = decrement_stock_for_sale(db, order_item.product_id, order_item.quantity)
            updated_stock_quantities[order_item.product_id] = new_quantity

            db.add(models.Interaction(
                user_id=current_user.id,
                product_id=order_item.product_id,
                interaction_type=models.InteractionType.PURCHASE,
                weight=5.0,
            ))

        order.status = models.OrderStatus.PAID
        order.stripe_payment_intent_id = session.payment_intent or session.id
        db.commit()

    except HTTPException:
        # Cas rare : stock épuisé entre la création de la session et le paiement.
        # Le client a bien payé sur Stripe -- il faudrait rembourser manuellement
        # en conditions réelles ; on marque la commande annulée pour investigation.
        order.status = models.OrderStatus.CANCELLED
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stock épuisé entre-temps. Le paiement a été reçu ; contactez le support pour remboursement.",
        )

    for product_id, new_quantity in updated_stock_quantities.items():
        manager.broadcast_from_sync({
            "type": "stock_update",
            "product_id": product_id,
            "quantity": new_quantity,
        })

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