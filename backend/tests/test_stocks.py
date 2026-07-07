"""Tests des endpoints de gestion des stocks."""

from tests.conftest import register_user, create_product_with_stock, get_seller_id_for_user


def _make_seller_and_product(client, db_session, email="stock_seller@test.com", quantity=20):
    _, headers = register_user(client, email=email, role="seller")
    from app import models
    user = db_session.query(models.User).filter_by(email=email).first()
    seller_id = get_seller_id_for_user(db_session, user.id)
    product = create_product_with_stock(db_session, seller_id, quantity=quantity)
    db_session.commit()
    return headers, product


def test_get_stock_returns_current_quantity(client, db_session):
    _, product = _make_seller_and_product(client, db_session, quantity=33)

    response = client.get(f"/stocks/{product.id}")
    assert response.status_code == 200
    assert response.json()["quantity"] == 33


def test_get_stock_unknown_product_returns_404(client):
    response = client.get("/stocks/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_adjust_stock_as_owner_increases_quantity(client, db_session):
    headers, product = _make_seller_and_product(client, db_session, quantity=10)

    response = client.post(
        f"/stocks/{product.id}/adjust",
        json={"quantity_delta": 15, "reason": "Réassort test"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["quantity"] == 25


def test_adjust_stock_as_non_owner_forbidden(client, db_session):
    _, product = _make_seller_and_product(client, db_session, email="owner@test.com")
    _, other_headers = register_user(client, email="intrus@test.com", role="seller")

    response = client.post(
        f"/stocks/{product.id}/adjust",
        json={"quantity_delta": 5},
        headers=other_headers,
    )
    assert response.status_code == 403


def test_adjust_stock_buyer_role_forbidden(client, db_session):
    _, product = _make_seller_and_product(client, db_session)
    _, buyer_headers = register_user(client, email="simple_buyer@test.com", role="buyer")

    response = client.post(
        f"/stocks/{product.id}/adjust",
        json={"quantity_delta": 5},
        headers=buyer_headers,
    )
    assert response.status_code == 403


def test_adjust_stock_cannot_go_negative(client, db_session):
    headers, product = _make_seller_and_product(client, db_session, quantity=5)

    response = client.post(
        f"/stocks/{product.id}/adjust",
        json={"quantity_delta": -10, "reason": "Correction excessive"},
        headers=headers,
    )
    assert response.status_code == 400
