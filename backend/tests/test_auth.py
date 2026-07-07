"""Tests des endpoints d'authentification."""

from tests.conftest import register_user


def test_register_creates_buyer_and_returns_token(client):
    response = client.post(
        "/auth/register",
        json={"email": "buyer@test.com", "password": "SecurePass1", "full_name": "Ali Buyer", "role": "buyer"},
    )
    assert response.status_code == 201
    body = response.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


def test_register_seller_creates_seller_profile(client, db_session):
    from app import models

    response = client.post(
        "/auth/register",
        json={"email": "seller@test.com", "password": "SecurePass1", "full_name": "Sara Seller", "role": "seller"},
    )
    assert response.status_code == 201

    user = db_session.query(models.User).filter_by(email="seller@test.com").first()
    assert user is not None
    assert user.role == models.UserRole.SELLER

    seller_profile = db_session.query(models.Seller).filter_by(user_id=user.id).first()
    assert seller_profile is not None


def test_register_duplicate_email_returns_409(client):
    payload = {"email": "dupe@test.com", "password": "SecurePass1", "role": "buyer"}
    first = client.post("/auth/register", json=payload)
    assert first.status_code == 201

    second = client.post("/auth/register", json=payload)
    assert second.status_code == 409


def test_login_success(client):
    client.post(
        "/auth/register",
        json={"email": "login@test.com", "password": "SecurePass1", "role": "buyer"},
    )
    response = client.post("/auth/login", json={"email": "login@test.com", "password": "SecurePass1"})
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_wrong_password_returns_401(client):
    client.post(
        "/auth/register",
        json={"email": "wrongpass@test.com", "password": "SecurePass1", "role": "buyer"},
    )
    response = client.post("/auth/login", json={"email": "wrongpass@test.com", "password": "IncorrectPass"})
    assert response.status_code == 401


def test_login_unknown_email_returns_401(client):
    response = client.post("/auth/login", json={"email": "ghost@test.com", "password": "whatever123"})
    assert response.status_code == 401


def test_me_returns_current_user_with_valid_token(client):
    _, headers = register_user(client, email="me@test.com")
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["email"] == "me@test.com"


def test_me_rejects_missing_token(client):
    response = client.get("/auth/me")
    assert response.status_code in (401, 403)  # HTTPBearer renvoie 403 si aucun header n'est fourni


def test_me_rejects_invalid_token(client):
    response = client.get("/auth/me", headers={"Authorization": "Bearer token_invalide"})
    assert response.status_code == 401
