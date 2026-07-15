"""
Tests des endpoints de commandes.

Le point le plus important à couvrir ici est l'ATOMICITÉ : si une commande contient
plusieurs lignes et que l'une d'elles échoue (stock insuffisant), aucune des lignes
ne doit être appliquée -- ni la commande, ni les décréments de stock déjà entamés.
"""

from tests.conftest import register_user, create_product_with_stock, get_seller_id_for_user

SHIPPING = {
    "shipping_address": "12 Rue de la Paix, Casablanca",
    "shipping_phone": "+212600000000",
}


def _make_seller_and_products(client, db_session, email="order_seller@test.com", quantities=(10, 3)):
    _, seller_headers = register_user(client, email=email, role="seller")
    from app import models
    user = db_session.query(models.User).filter_by(email=email).first()
    seller_id = get_seller_id_for_user(db_session, user.id)

    products = [
        create_product_with_stock(db_session, seller_id, name=f"Produit commande {i}", price=20.0, quantity=q)
        for i, q in enumerate(quantities)
    ]
    db_session.commit()
    return products


def test_create_order_decrements_stock_and_creates_interaction(client, db_session):
    products = _make_seller_and_products(client, db_session, quantities=(10,))
    product = products[0]
    _, buyer_headers = register_user(client, email="order_buyer@test.com", role="buyer")

    response = client.post(
        "/orders",
        json={"items": [{"product_id": product.id, "quantity": 3}], **SHIPPING},
        headers=buyer_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "paid"
    assert body["total_amount"] == 60.0  # 20.0 * 3
    assert body["shipping_address"] == SHIPPING["shipping_address"]
    assert body["shipping_phone"] == SHIPPING["shipping_phone"]
    assert body["shipping_email"] == "order_buyer@test.com"

    stock_response = client.get(f"/stocks/{product.id}")
    assert stock_response.json()["quantity"] == 7  # 10 - 3

    from app import models
    interactions = db_session.query(models.Interaction).filter_by(
        product_id=product.id, interaction_type=models.InteractionType.PURCHASE
    ).all()
    assert len(interactions) == 1


def test_create_order_missing_shipping_info_returns_422(client, db_session):
    products = _make_seller_and_products(client, db_session, quantities=(10,))
    product = products[0]
    _, buyer_headers = register_user(client, email="no_shipping@test.com", role="buyer")

    response = client.post(
        "/orders",
        json={"items": [{"product_id": product.id, "quantity": 1}]},  # pas d'adresse/téléphone
        headers=buyer_headers,
    )
    assert response.status_code == 422


def test_create_order_multi_line_success(client, db_session):
    products = _make_seller_and_products(client, db_session, quantities=(10, 10))
    _, buyer_headers = register_user(client, email="multi_buyer@test.com", role="buyer")

    response = client.post(
        "/orders",
        json={
            "items": [
                {"product_id": products[0].id, "quantity": 2},
                {"product_id": products[1].id, "quantity": 4},
            ],
            **SHIPPING,
        },
        headers=buyer_headers,
    )
    assert response.status_code == 201
    assert response.json()["total_amount"] == 120.0  # (2 + 4) * 20.0
    assert len(response.json()["items"]) == 2


def test_create_order_insufficient_stock_rolls_back_everything(client, db_session):
    """
    Le premier produit a assez de stock, le second n'en a pas assez.
    Toute la commande doit échouer, ET le stock du premier produit ne doit
    PAS avoir été décrémenté (pas de commande "à moitié" appliquée).
    """
    products = _make_seller_and_products(client, db_session, quantities=(10, 2))
    ok_product, insufficient_product = products
    _, buyer_headers = register_user(client, email="rollback_buyer@test.com", role="buyer")

    response = client.post(
        "/orders",
        json={
            "items": [
                {"product_id": ok_product.id, "quantity": 3},
                {"product_id": insufficient_product.id, "quantity": 10},  # seulement 2 disponibles
            ],
            **SHIPPING,
        },
        headers=buyer_headers,
    )
    assert response.status_code == 400

    # Le stock du PREMIER produit (qui aurait pu être décrémenté avant l'échec)
    # doit être resté intact grâce au rollback transactionnel.
    stock_response = client.get(f"/stocks/{ok_product.id}")
    assert stock_response.json()["quantity"] == 10

    from app import models
    orders_count = db_session.query(models.Order).count()
    assert orders_count == 0


def test_create_order_empty_items_returns_400(client):
    _, buyer_headers = register_user(client, email="empty_order@test.com", role="buyer")

    response = client.post("/orders", json={"items": [], **SHIPPING}, headers=buyer_headers)
    assert response.status_code == 400


def test_create_order_unknown_product_returns_404(client):
    _, buyer_headers = register_user(client, email="ghost_product@test.com", role="buyer")

    response = client.post(
        "/orders",
        json={
            "items": [{"product_id": "00000000-0000-0000-0000-000000000000", "quantity": 1}],
            **SHIPPING,
        },
        headers=buyer_headers,
    )
    assert response.status_code == 404


def test_list_my_orders_only_returns_own_orders(client, db_session):
    products = _make_seller_and_products(client, db_session, quantities=(10,))
    product = products[0]

    _, buyer_a_headers = register_user(client, email="buyer_a@test.com", role="buyer")
    _, buyer_b_headers = register_user(client, email="buyer_b@test.com", role="buyer")

    client.post(
        "/orders",
        json={"items": [{"product_id": product.id, "quantity": 1}], **SHIPPING},
        headers=buyer_a_headers,
    )

    response_a = client.get("/orders", headers=buyer_a_headers)
    response_b = client.get("/orders", headers=buyer_b_headers)

    assert len(response_a.json()) == 1
    assert len(response_b.json()) == 0


def test_get_order_detail_forbidden_for_other_user(client, db_session):
    products = _make_seller_and_products(client, db_session, quantities=(10,))
    product = products[0]

    _, owner_headers = register_user(client, email="order_owner@test.com", role="buyer")
    _, intruder_headers = register_user(client, email="order_intruder@test.com", role="buyer")

    create_response = client.post(
        "/orders",
        json={"items": [{"product_id": product.id, "quantity": 1}], **SHIPPING},
        headers=owner_headers,
    )
    order_id = create_response.json()["id"]

    response = client.get(f"/orders/{order_id}", headers=intruder_headers)
    assert response.status_code == 403


def test_seller_sees_orders_containing_their_products_with_buyer_info(client, db_session):
    products = _make_seller_and_products(client, db_session, email="seller_view@test.com", quantities=(10,))
    product = products[0]
    _, buyer_headers = register_user(
        client, email="visible_buyer@test.com", role="buyer", full_name="Amine Buyer"
    )

    client.post(
        "/orders",
        json={"items": [{"product_id": product.id, "quantity": 2}], **SHIPPING},
        headers=buyer_headers,
    )

    login_response = client.post(
        "/auth/login", json={"email": "seller_view@test.com", "password": "TestPass123"}
    )
    token = login_response.json()["access_token"]
    seller_headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/dashboard/orders", headers=seller_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["buyer_email"] == "visible_buyer@test.com"
    assert body[0]["buyer_name"] == "Amine Buyer"
    assert body[0]["shipping_address"] == SHIPPING["shipping_address"]
    assert body[0]["total_amount"] == 40.0  # 20.0 * 2


def test_seller_orders_excludes_other_sellers_products(client, db_session):
    """Un vendeur ne doit voir AUCUNE commande qui ne contient pas au moins un de ses produits."""
    _make_seller_and_products(client, db_session, email="unrelated_seller@test.com", quantities=(10,))

    login_response = client.post(
        "/auth/login", json={"email": "unrelated_seller@test.com", "password": "TestPass123"}
    )
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/dashboard/orders", headers=headers)
    assert response.status_code == 200
    assert response.json() == []
