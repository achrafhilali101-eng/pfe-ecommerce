"""
Tests du flux de paiement Stripe Checkout.

Stripe est simulé (mocké) -- on ne fait jamais de vrai appel réseau vers
Stripe dans les tests, pour rester rapide et reproductible sans dépendre
d'une connexion internet ni de vraies clés API.
"""

from unittest.mock import patch, MagicMock

from tests.conftest import register_user, create_product_with_stock, get_seller_id_for_user

SHIPPING = {
    "shipping_address": "12 Rue de la Paix, Casablanca",
    "shipping_phone": "+212600000000",
}


def _make_seller_and_product(client, db_session, email="stripe_seller@test.com", quantity=10):
    _, seller_headers = register_user(client, email=email, role="seller")
    from app import models
    user = db_session.query(models.User).filter_by(email=email).first()
    seller_id = get_seller_id_for_user(db_session, user.id)
    product = create_product_with_stock(db_session, seller_id, price=20.0, quantity=quantity)
    db_session.commit()
    return product


def test_create_checkout_session_does_not_decrement_stock_yet(client, db_session):
    """Le stock ne doit être touché qu'à la CONFIRMATION du paiement, pas à la création de la session."""
    product = _make_seller_and_product(client, db_session, quantity=10)
    _, buyer_headers = register_user(client, email="checkout_buyer@test.com", role="buyer")

    fake_session = MagicMock(id="cs_test_123", url="https://checkout.stripe.com/test_session")

    with patch("stripe.checkout.Session.create", return_value=fake_session):
        response = client.post(
            "/orders/checkout-session",
            json={"items": [{"product_id": product.id, "quantity": 2}], **SHIPPING},
            headers=buyer_headers,
        )

    assert response.status_code == 201
    body = response.json()
    assert body["checkout_url"] == "https://checkout.stripe.com/test_session"
    assert "order_id" in body

    stock_response = client.get(f"/stocks/{product.id}")
    assert stock_response.json()["quantity"] == 10  # inchangé


def test_confirm_payment_decrements_stock_when_stripe_confirms(client, db_session):
    product = _make_seller_and_product(client, db_session, quantity=10)
    _, buyer_headers = register_user(client, email="confirm_buyer@test.com", role="buyer")

    fake_create = MagicMock(id="cs_test_456", url="https://checkout.stripe.com/xyz")
    with patch("stripe.checkout.Session.create", return_value=fake_create):
        create_response = client.post(
            "/orders/checkout-session",
            json={"items": [{"product_id": product.id, "quantity": 3}], **SHIPPING},
            headers=buyer_headers,
        )
    order_id = create_response.json()["order_id"]

    fake_retrieved = MagicMock(payment_status="paid", payment_intent="pi_test_456")
    with patch("stripe.checkout.Session.retrieve", return_value=fake_retrieved):
        confirm_response = client.get(
            f"/orders/{order_id}/confirm-payment",
            params={"session_id": "cs_test_456"},
            headers=buyer_headers,
        )

    assert confirm_response.status_code == 200
    assert confirm_response.json()["status"] == "paid"

    stock_response = client.get(f"/stocks/{product.id}")
    assert stock_response.json()["quantity"] == 7  # 10 - 3, décrémenté seulement maintenant


def test_confirm_payment_rejected_when_stripe_says_unpaid(client, db_session):
    product = _make_seller_and_product(client, db_session, quantity=10)
    _, buyer_headers = register_user(client, email="unpaid_buyer@test.com", role="buyer")

    fake_create = MagicMock(id="cs_test_789", url="https://checkout.stripe.com/xyz")
    with patch("stripe.checkout.Session.create", return_value=fake_create):
        create_response = client.post(
            "/orders/checkout-session",
            json={"items": [{"product_id": product.id, "quantity": 1}], **SHIPPING},
            headers=buyer_headers,
        )
    order_id = create_response.json()["order_id"]

    fake_retrieved = MagicMock(payment_status="unpaid")
    with patch("stripe.checkout.Session.retrieve", return_value=fake_retrieved):
        confirm_response = client.get(
            f"/orders/{order_id}/confirm-payment",
            params={"session_id": "cs_test_789"},
            headers=buyer_headers,
        )

    assert confirm_response.status_code == 400

    stock_response = client.get(f"/stocks/{product.id}")
    assert stock_response.json()["quantity"] == 10  # jamais décrémenté


def test_confirm_payment_is_idempotent(client, db_session):
    """Rafraîchir la page de confirmation deux fois ne doit pas décrémenter le stock deux fois."""
    product = _make_seller_and_product(client, db_session, quantity=10)
    _, buyer_headers = register_user(client, email="idempotent_buyer@test.com", role="buyer")

    fake_create = MagicMock(id="cs_test_999", url="https://checkout.stripe.com/xyz")
    with patch("stripe.checkout.Session.create", return_value=fake_create):
        create_response = client.post(
            "/orders/checkout-session",
            json={"items": [{"product_id": product.id, "quantity": 2}], **SHIPPING},
            headers=buyer_headers,
        )
    order_id = create_response.json()["order_id"]

    fake_retrieved = MagicMock(payment_status="paid", payment_intent="pi_test_999")
    with patch("stripe.checkout.Session.retrieve", return_value=fake_retrieved):
        client.get(f"/orders/{order_id}/confirm-payment", params={"session_id": "cs_test_999"}, headers=buyer_headers)
        second_response = client.get(
            f"/orders/{order_id}/confirm-payment", params={"session_id": "cs_test_999"}, headers=buyer_headers
        )

    assert second_response.status_code == 200
    stock_response = client.get(f"/stocks/{product.id}")
    assert stock_response.json()["quantity"] == 8  # décrémenté une seule fois (10 - 2)


def test_create_checkout_session_insufficient_stock_returns_400(client, db_session):
    product = _make_seller_and_product(client, db_session, quantity=2)
    _, buyer_headers = register_user(client, email="checkout_insufficient@test.com", role="buyer")

    response = client.post(
        "/orders/checkout-session",
        json={"items": [{"product_id": product.id, "quantity": 5}], **SHIPPING},
        headers=buyer_headers,
    )
    assert response.status_code == 400
