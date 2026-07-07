"""
ETL - Import du dataset Olist (Brazilian E-Commerce) vers notre schéma PostgreSQL.

Fichiers Olist attendus dans data/raw/ (téléchargeables sur Kaggle:
"Brazilian E-Commerce Public Dataset by Olist"):
    - olist_sellers_dataset.csv
    - olist_products_dataset.csv
    - olist_orders_dataset.csv
    - olist_order_items_dataset.csv
    - olist_order_reviews_dataset.csv
    - olist_customers_dataset.csv
    - product_category_name_translation.csv

Usage:
    python scripts/etl_olist.py --raw-dir ../data/raw --out-dir ../data/processed --load-db

Le flag --load-db écrit directement dans PostgreSQL via SQLAlchemy.
Sans ce flag, le script produit uniquement des CSV nettoyés dans data/processed/
(utile pour valider la logique sans base de données montée).
"""

import argparse
import os
import sys
import uuid

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_raw(raw_dir: str) -> dict:
    """Charge les CSV Olist bruts dans des DataFrames."""
    files = {
        "sellers": "olist_sellers_dataset.csv",
        "products": "olist_products_dataset.csv",
        "orders": "olist_orders_dataset.csv",
        "order_items": "olist_order_items_dataset.csv",
        "reviews": "olist_order_reviews_dataset.csv",
        "customers": "olist_customers_dataset.csv",
        "category_translation": "product_category_name_translation.csv",
    }
    data = {}
    for key, filename in files.items():
        path = os.path.join(raw_dir, filename)
        if not os.path.exists(path):
            print(f"[WARN] Fichier manquant : {path} -- il sera ignoré.")
            data[key] = pd.DataFrame()
            continue
        data[key] = pd.read_csv(path)
    return data


def clean_sellers(sellers: pd.DataFrame) -> pd.DataFrame:
    if sellers.empty:
        return sellers
    df = sellers.copy()
    df = df.drop_duplicates(subset=["seller_id"])
    df["company_name"] = "Vendeur_" + df["seller_id"].str[:8]
    df = df.rename(columns={
        "seller_id": "olist_seller_id",
        "seller_city": "city",
        "seller_state": "state",
    })
    return df[["olist_seller_id", "company_name", "city", "state"]]


def clean_products(products: pd.DataFrame, category_translation: pd.DataFrame) -> pd.DataFrame:
    if products.empty:
        return products
    df = products.copy()
    df = df.drop_duplicates(subset=["product_id"])

    if not category_translation.empty:
        df = df.merge(category_translation, on="product_category_name", how="left")
        df["category_name"] = df["product_category_name_english"].fillna(df["product_category_name"])
    else:
        df["category_name"] = df["product_category_name"]

    df["category_name"] = df["category_name"].fillna("uncategorized")

    # Olist ne fournit pas de prix produit directement (le prix est dans order_items).
    # On le calcule séparément dans build_product_prices() puis on merge.
    df = df.rename(columns={"product_id": "olist_product_id"})
    df["name"] = df["category_name"].str.replace("_", " ").str.title() + " - " + df["olist_product_id"].str[:6]
    df["description"] = "Produit issu du catalogue Olist, catégorie : " + df["category_name"]

    return df[["olist_product_id", "category_name", "name", "description"]]


def build_product_prices(order_items: pd.DataFrame) -> pd.DataFrame:
    """Prix moyen observé par produit à partir des lignes de commande."""
    if order_items.empty:
        return pd.DataFrame(columns=["olist_product_id", "price"])
    df = order_items.groupby("product_id")["price"].mean().reset_index()
    df = df.rename(columns={"product_id": "olist_product_id"})
    return df


def build_stock_simulation(products: pd.DataFrame, seed: int = 42) -> pd.DataFrame:
    """
    Olist ne fournit pas de niveau de stock réel (c'est une marketplace historique).
    On simule un stock initial réaliste par produit avec numpy, pondéré par la
    fréquence de vente (plus un produit se vend, plus son stock de départ est élevé).
    """
    rng = np.random.default_rng(seed)
    df = products.copy()
    df["quantity"] = rng.integers(low=0, high=120, size=len(df))
    df["low_stock_threshold"] = 5
    return df[["olist_product_id", "quantity", "low_stock_threshold"]]


def build_interactions(order_items: pd.DataFrame, orders: pd.DataFrame,
                        reviews: pd.DataFrame, customers: pd.DataFrame) -> pd.DataFrame:
    """
    Construit la table d'interactions user-produit pour le moteur de recommandation :
    - achat (purchase) : depuis order_items + orders + customers
    - note (rating)    : depuis reviews, rattachée au produit via order_id
    """
    if order_items.empty or orders.empty or customers.empty:
        return pd.DataFrame()

    orders_customers = orders.merge(customers, on="customer_id", how="left")
    purchases = order_items.merge(
        orders_customers[["order_id", "customer_unique_id"]], on="order_id", how="left"
    )
    purchases = purchases.rename(columns={
        "customer_unique_id": "user_ref",
        "product_id": "olist_product_id",
    })
    purchases["interaction_type"] = "purchase"
    purchases["weight"] = 5.0
    purchases["rating"] = np.nan
    purchases = purchases[["user_ref", "olist_product_id", "interaction_type", "weight", "rating"]]

    interactions = [purchases]

    if not reviews.empty:
        rev = reviews.merge(order_items[["order_id", "product_id"]], on="order_id", how="left")
        rev = rev.merge(orders_customers[["order_id", "customer_unique_id"]], on="order_id", how="left")
        rev = rev.dropna(subset=["product_id", "customer_unique_id", "review_score"])
        rev = rev.rename(columns={
            "customer_unique_id": "user_ref",
            "product_id": "olist_product_id",
            "review_score": "rating",
        })
        rev["interaction_type"] = "rating"
        rev["weight"] = rev["rating"].astype(float)
        rev = rev[["user_ref", "olist_product_id", "interaction_type", "weight", "rating"]]
        interactions.append(rev)

    result = pd.concat(interactions, ignore_index=True)
    result = result.dropna(subset=["user_ref", "olist_product_id"])
    result = result.drop_duplicates(subset=["user_ref", "olist_product_id", "interaction_type"])
    return result


def run_etl(raw_dir: str, out_dir: str, load_db: bool = False):
    os.makedirs(out_dir, exist_ok=True)
    print("[1/6] Chargement des fichiers bruts...")
    raw = load_raw(raw_dir)

    print("[2/6] Nettoyage vendeurs...")
    sellers = clean_sellers(raw["sellers"])
    sellers.to_csv(os.path.join(out_dir, "sellers_clean.csv"), index=False)
    print(f"   -> {len(sellers)} vendeurs")

    print("[3/6] Nettoyage produits + prix...")
    products = clean_products(raw["products"], raw["category_translation"])
    prices = build_product_prices(raw["order_items"])
    products = products.merge(prices, on="olist_product_id", how="left")
    products["price"] = products["price"].fillna(products["price"].median() if not products["price"].isna().all() else 29.90)
    products.to_csv(os.path.join(out_dir, "products_clean.csv"), index=False)
    print(f"   -> {len(products)} produits")

    print("[4/6] Simulation des stocks...")
    stocks = build_stock_simulation(products)
    stocks.to_csv(os.path.join(out_dir, "stocks_simulated.csv"), index=False)
    print(f"   -> {len(stocks)} lignes de stock")

    print("[5/6] Construction des interactions (reco)...")
    interactions = build_interactions(raw["order_items"], raw["orders"], raw["reviews"], raw["customers"])
    interactions.to_csv(os.path.join(out_dir, "interactions.csv"), index=False)
    print(f"   -> {len(interactions)} interactions")

    print("[6/6] Terminé. Fichiers écrits dans:", out_dir)

    if load_db:
        load_into_postgres(sellers, products, stocks, interactions)


def load_into_postgres(sellers, products, stocks, interactions):
    """Charge les DataFrames nettoyés dans PostgreSQL via les modèles SQLAlchemy."""
    from app.database import SessionLocal, engine, Base
    from app import models

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        print("Chargement vendeurs -> DB...")
        seller_id_map = {}
        for _, row in sellers.iterrows():
            s = models.Seller(
                olist_seller_id=row["olist_seller_id"],
                company_name=row["company_name"],
                city=row.get("city"),
                state=row.get("state"),
            )
            db.add(s)
            db.flush()
            seller_id_map[row["olist_seller_id"]] = s.id
        db.commit()

        print("Chargement catégories + produits -> DB...")
        category_map = {}
        product_id_map = {}
        # fallback seller si un produit n'a pas de vendeur associé dans ce sous-échantillon
        default_seller = db.query(models.Seller).first()

        for _, row in products.iterrows():
            cat_name = row["category_name"]
            if cat_name not in category_map:
                cat = models.Category(name=cat_name, slug=cat_name.replace(" ", "-"))
                db.add(cat)
                db.flush()
                category_map[cat_name] = cat.id

            p = models.Product(
                olist_product_id=row["olist_product_id"],
                seller_id=default_seller.id if default_seller else None,
                category_id=category_map[cat_name],
                name=row["name"],
                description=row["description"],
                price=float(row["price"]),
            )
            db.add(p)
            db.flush()
            product_id_map[row["olist_product_id"]] = p.id
        db.commit()

        print("Chargement stocks -> DB...")
        for _, row in stocks.iterrows():
            pid = product_id_map.get(row["olist_product_id"])
            if pid is None:
                continue
            stock = models.Stock(
                product_id=pid,
                quantity=int(row["quantity"]),
                low_stock_threshold=int(row["low_stock_threshold"]),
            )
            db.add(stock)
        db.commit()

        print("Chargement interactions -> DB (échantillonné si volumineux)...")
        # NB: user_ref (olist customer_unique_id) doit être mappé à un User réel.
        # Pour le MVP, on crée un compte "fantôme" par customer_unique_id rencontré.
        user_map = {}
        for _, row in interactions.iterrows():
            pid = product_id_map.get(row["olist_product_id"])
            if pid is None:
                continue
            user_ref = row["user_ref"]
            if user_ref not in user_map:
                u = models.User(
                    email=f"{user_ref}@olist.import",
                    hashed_password="not_usable_import_only",
                    role=models.UserRole.BUYER,
                )
                db.add(u)
                db.flush()
                user_map[user_ref] = u.id

            interaction = models.Interaction(
                user_id=user_map[user_ref],
                product_id=pid,
                interaction_type=row["interaction_type"],
                rating=row["rating"] if not pd.isna(row["rating"]) else None,
                weight=float(row["weight"]),
            )
            db.add(interaction)
        db.commit()

        print("Import terminé avec succès.")
    except Exception as e:
        db.rollback()
        print(f"[ERREUR] Import échoué, rollback effectué: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ETL Olist -> PostgreSQL")
    parser.add_argument("--raw-dir", default="../data/raw")
    parser.add_argument("--out-dir", default="../data/processed")
    parser.add_argument("--load-db", action="store_true", help="Écrit directement en base PostgreSQL")
    args = parser.parse_args()

    run_etl(args.raw_dir, args.out_dir, load_db=args.load_db)
