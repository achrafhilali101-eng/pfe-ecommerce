"""Tests des endpoints catalogue (produits, catégories)."""

from tests.conftest import register_user, create_category, create_product_with_stock, get_seller_id_for_user


def _make_seller_with_products(client, db_session, n=3, category_name="informatique"):
    _, headers = register_user(client, email="cat_seller@test.com", role="seller")
    from app import models
    user = db_session.query(models.User).filter_by(email="cat_seller@test.com").first()
    seller_id = get_seller_id_for_user(db_session, user.id)

    category = create_category(db_session, name=category_name)
    products = [
        create_product_with_stock(db_session, seller_id, category_id=category.id, name=f"Produit {i}", price=10 * (i + 1))
        for i in range(n)
    ]
    db_session.commit()
    return headers, category, products


def test_list_products_returns_paginated_results(client, db_session):
    _make_seller_with_products(client, db_session, n=5)

    response = client.get("/products", params={"page": 1, "page_size": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2


def test_list_products_search_filters_by_name(client, db_session):
    _make_seller_with_products(client, db_session, n=3)

    response = client.get("/products", params={"search": "Produit 1"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Produit 1"


def test_list_products_filters_by_category(client, db_session):
    headers, category, products = _make_seller_with_products(client, db_session, n=2, category_name="mode")

    response = client.get("/products", params={"category_id": category.id})
    assert response.status_code == 200
    assert response.json()["total"] == 2


def test_get_product_detail_includes_category_and_stock(client, db_session):
    headers, category, products = _make_seller_with_products(client, db_session, n=1)
    product = products[0]

    response = client.get(f"/products/{product.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == product.name
    assert body["category"]["id"] == category.id
    assert body["stock"]["quantity"] == 50


def test_get_product_not_found_returns_404(client):
    response = client.get("/products/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_list_categories_returns_all(client, db_session):
    create_category(db_session, name="beaute")
    create_category(db_session, name="sport")
    db_session.commit()

    response = client.get("/categories")
    assert response.status_code == 200
    names = [c["name"] for c in response.json()]
    assert "beaute" in names
    assert "sport" in names


def test_create_product_requires_seller_role(client):
    _, headers = register_user(client, email="just_buyer@test.com", role="buyer")

    response = client.post(
        "/products",
        json={"name": "Produit interdit", "price": 15.0},
        headers=headers,
    )
    assert response.status_code == 403


def test_create_product_as_seller_succeeds(client):
    _, headers = register_user(client, email="new_seller@test.com", role="seller")

    response = client.post(
        "/products",
        json={"name": "Nouveau produit", "price": 42.0, "initial_stock": 10},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Nouveau produit"
    assert body["price"] == 42.0
    assert body["stock"]["quantity"] == 10


def test_create_product_without_auth_returns_401_or_403(client):
    response = client.post("/products", json={"name": "Sans auth", "price": 10.0})
    assert response.status_code in (401, 403)
