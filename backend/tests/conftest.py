"""
Configuration pytest partagée.

Stratégie d'isolation des tests :
- Les modèles utilisent des colonnes UUID spécifiques à PostgreSQL (dialecte
  postgresql.UUID), donc les tests tournent contre une VRAIE base Postgres de
  test (pas SQLite) -- séparée de la base de développement pour ne jamais
  toucher aux données réelles.
- Chaque test s'exécute dans une transaction ouverte puis annulée (rollback) à
  la fin -- même si le code de l'application appelle `db.commit()`, grâce au
  pattern SAVEPOINT ci-dessous. Résultat : aucune donnée ne persiste entre les
  tests, sans avoir à vider les tables manuellement.

Prérequis avant de lancer les tests (une seule fois) :
    docker exec -it pfe_postgres psql -U pfe_user -d postgres -c "CREATE DATABASE pfe_ecommerce_test;"

Lancement :
    cd backend
    pytest
"""

import os
import sys

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base, get_db
from app.main import app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg2://pfe_user:pfe_password@localhost:5433/pfe_ecommerce_test",
)

engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def create_test_schema():
    """Crée toutes les tables au début de la session de test, les supprime à la fin."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db_session():
    """
    Fournit une session dont TOUTES les écritures (même après un `commit()` fait
    par le code applicatif) sont annulées à la fin du test.

    Pattern standard SQLAlchemy : la session est liée à une connexion sur
    laquelle on a ouvert une transaction externe + un SAVEPOINT. Un `commit()`
    dans le code applicatif ne referme que le SAVEPOINT ; l'écouteur
    `after_transaction_end` en rouvre un immédiatement. Seul le rollback de la
    transaction externe (à la fin du test) efface réellement les données.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, trans):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    """TestClient FastAPI dont la dépendance get_db est remplacée par la session de test."""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------- Helpers réutilisés par plusieurs fichiers de tests ----------

def register_user(client, email="user@test.com", password="TestPass123", role="buyer", full_name="Test User"):
    """Inscrit un utilisateur et retourne (token, headers_authorization)."""
    response = client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": full_name, "role": role},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    return token, {"Authorization": f"Bearer {token}"}


def create_category(db_session, name="test_category"):
    from app import models
    category = models.Category(name=name, slug=name.replace(" ", "-"))
    db_session.add(category)
    db_session.flush()
    return category


def create_product_with_stock(db_session, seller_id, category_id=None, name="Produit Test",
                               price=29.90, quantity=50, low_stock_threshold=5):
    from app import models
    product = models.Product(
        seller_id=seller_id,
        category_id=category_id,
        name=name,
        description="Description de test",
        price=price,
    )
    db_session.add(product)
    db_session.flush()

    stock = models.Stock(product_id=product.id, quantity=quantity, low_stock_threshold=low_stock_threshold)
    db_session.add(stock)
    db_session.flush()

    return product


def get_seller_id_for_user(db_session, user_id):
    from app import models
    seller = db_session.query(models.Seller).filter(models.Seller.user_id == user_id).first()
    return seller.id if seller else None
