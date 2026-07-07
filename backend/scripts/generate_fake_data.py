"""
Génère des données synthétiques complémentaires pour enrichir le dataset Olist réel :
- comptes utilisateurs "acheteurs" utilisables (email/mot de passe connus, pour tester la démo)
- comptes vendeurs de démo
- historique de mouvements de stock (pour peupler les graphiques du dashboard)
- interactions de vue/ajout panier supplémentaires (Olist ne contient que des achats/notes,
  pas de vues -> on simule le comportement de navigation pour enrichir la reco)

Usage:
    python scripts/generate_fake_data.py --load-db
"""

import argparse
import os
import sys
import random
from datetime import datetime, timedelta

from faker import Faker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fake = Faker()
Faker.seed(42)
random.seed(42)

DEMO_PASSWORD_HASH = None  # calculé à l'exécution avec passlib


def get_password_hash(password: str) -> str:
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    return pwd_context.hash(password)


def generate_demo_users(n_buyers: int = 15, n_sellers: int = 3):
    """Comptes de démonstration avec identifiants connus, pour la soutenance."""
    buyers = []
    for i in range(n_buyers):
        buyers.append({
            "email": f"buyer{i+1}@demo.com",
            "password": "Demo1234!",
            "full_name": fake.name(),
        })

    sellers = []
    for i in range(n_sellers):
        sellers.append({
            "email": f"seller{i+1}@demo.com",
            "password": "Demo1234!",
            "full_name": fake.name(),
            "company_name": fake.company(),
            "city": fake.city(),
        })

    return buyers, sellers


def generate_stock_movement_history(product_ids: list, days: int = 30):
    """Simule un historique de mouvements de stock sur N jours (pour graphiques dashboard)."""
    movements = []
    now = datetime.utcnow()
    for pid in product_ids:
        n_events = random.randint(3, 12)
        for _ in range(n_events):
            movement_type = random.choices(
                ["restock", "sale", "adjustment", "return"],
                weights=[0.2, 0.65, 0.1, 0.05],
            )[0]
            delta = (
                random.randint(20, 80) if movement_type == "restock"
                else -random.randint(1, 5) if movement_type == "sale"
                else random.randint(-3, 3)
            )
            movements.append({
                "product_id": pid,
                "movement_type": movement_type,
                "quantity_delta": delta,
                "reason": f"Généré automatiquement ({movement_type})",
                "created_at": now - timedelta(days=random.randint(0, days)),
            })
    return movements


def generate_browsing_interactions(user_ids: list, product_ids: list, n_events: int = 500):
    """
    Simule des vues et ajouts au panier (signal implicite absent d'Olist)
    pour rendre la matrice de reco moins creuse (cold-start atténué).
    """
    events = []
    for _ in range(n_events):
        user_id = random.choice(user_ids)
        product_id = random.choice(product_ids)
        interaction_type = random.choices(["view", "add_to_cart"], weights=[0.8, 0.2])[0]
        weight = 1.0 if interaction_type == "view" else 2.0
        events.append({
            "user_id": user_id,
            "product_id": product_id,
            "interaction_type": interaction_type,
            "weight": weight,
        })
    return events


def load_into_db(buyers, sellers, n_browsing_events=500, stock_history_days=30):
    from app.database import SessionLocal, engine, Base
    from app import models

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        print("Création des comptes acheteurs de démo...")
        buyer_ids = []
        for b in buyers:
            existing = db.query(models.User).filter_by(email=b["email"]).first()
            if existing:
                buyer_ids.append(existing.id)
                continue
            u = models.User(
                email=b["email"],
                hashed_password=get_password_hash(b["password"]),
                full_name=b["full_name"],
                role=models.UserRole.BUYER,
            )
            db.add(u)
            db.flush()
            buyer_ids.append(u.id)
        db.commit()

        print("Création des comptes vendeurs de démo...")
        for s in sellers:
            existing = db.query(models.User).filter_by(email=s["email"]).first()
            if existing:
                continue
            u = models.User(
                email=s["email"],
                hashed_password=get_password_hash(s["password"]),
                full_name=s["full_name"],
                role=models.UserRole.SELLER,
            )
            db.add(u)
            db.flush()
            seller_profile = models.Seller(
                user_id=u.id,
                company_name=s["company_name"],
                city=s["city"],
            )
            db.add(seller_profile)
        db.commit()

        product_ids = [p.id for p in db.query(models.Product.id).all()]
        if not product_ids:
            print("[WARN] Aucun produit en base -- lance d'abord etl_olist.py --load-db")
            return

        print(f"Génération de {n_browsing_events} interactions de navigation...")
        events = generate_browsing_interactions(buyer_ids, product_ids, n_browsing_events)
        for e in events:
            interaction = models.Interaction(**e)
            db.add(interaction)
        db.commit()

        print(f"Génération de l'historique de mouvements de stock ({stock_history_days} jours)...")
        movements = generate_stock_movement_history(product_ids, stock_history_days)
        for m in movements:
            mv = models.StockMovement(**m)
            db.add(mv)
        db.commit()

        print("Données synthétiques chargées avec succès.")
        print(f"-> {len(buyers)} comptes acheteurs (mot de passe: Demo1234!)")
        print(f"-> {len(sellers)} comptes vendeurs (mot de passe: Demo1234!)")

    except Exception as e:
        db.rollback()
        print(f"[ERREUR] {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-buyers", type=int, default=15)
    parser.add_argument("--n-sellers", type=int, default=3)
    parser.add_argument("--n-browsing-events", type=int, default=500)
    parser.add_argument("--stock-history-days", type=int, default=30)
    parser.add_argument("--load-db", action="store_true")
    args = parser.parse_args()

    buyers, sellers = generate_demo_users(args.n_buyers, args.n_sellers)

    if args.load_db:
        load_into_db(buyers, sellers, args.n_browsing_events, args.stock_history_days)
    else:
        print("Mode dry-run (pas de --load-db). Exemple de données générées :")
        print(buyers[:2])
        print(sellers[:1])
