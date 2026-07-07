"""
Entraîne le moteur de recommandation à partir de la table `interactions`.

Approche : collaborative filtering par factorisation matricielle (TruncatedSVD),
choisi plutôt que la librairie `implicit` (abandonnée : nécessite une compilation
C++/CMake indisponible sans Visual Studio sur Windows). TruncatedSVD est fourni par
scikit-learn (déjà une dépendance du projet), sans compilation native requise.

Sortie : artefacts sauvegardés dans app/ml/artifacts/ :
  - user_factors.joblib      : matrice (n_users, k) des facteurs latents utilisateurs
  - item_factors.joblib      : matrice (n_items, k) des facteurs latents produits
  - user_id_to_idx.joblib    : mapping UUID utilisateur -> index de ligne
  - idx_to_product_id.joblib : mapping index de colonne -> UUID produit
  - train_matrix.joblib      : matrice sparse user-item d'entraînement (pour exclure
                                les produits déjà vus lors de la recommandation)
  - popularity_ranking.joblib: liste des UUID produits triés par popularité (fallback
                                cold-start pour les nouveaux utilisateurs)

Usage:
    python -m app.ml.train_recommender --n-components 20 --top-k 10
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
import joblib
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

ARTIFACTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")


def load_interactions() -> pd.DataFrame:
    """Charge la table interactions depuis PostgreSQL, agrégée par (user, produit)."""
    from app.database import SessionLocal
    from app import models

    db = SessionLocal()
    try:
        rows = db.query(
            models.Interaction.user_id,
            models.Interaction.product_id,
            models.Interaction.weight,
        ).all()
    finally:
        db.close()

    df = pd.DataFrame(rows, columns=["user_id", "product_id", "weight"])
    # Un même couple (user, produit) peut avoir plusieurs lignes (ex: purchase + rating)
    # -> on somme les poids pour obtenir un signal de préférence global.
    df = df.groupby(["user_id", "product_id"], as_index=False)["weight"].sum()
    return df


def build_sparse_matrix(df: pd.DataFrame):
    """Construit la matrice sparse user-item + les mappings id <-> index."""
    user_ids = df["user_id"].unique()
    product_ids = df["product_id"].unique()

    user_id_to_idx = {uid: i for i, uid in enumerate(user_ids)}
    product_id_to_idx = {pid: i for i, pid in enumerate(product_ids)}
    idx_to_product_id = {i: pid for pid, i in product_id_to_idx.items()}

    rows = df["user_id"].map(user_id_to_idx).values
    cols = df["product_id"].map(product_id_to_idx).values
    vals = df["weight"].values

    matrix = csr_matrix((vals, (rows, cols)), shape=(len(user_ids), len(product_ids)))
    return matrix, user_id_to_idx, idx_to_product_id


def train_test_split_interactions(df: pd.DataFrame, test_ratio: float = 0.2, seed: int = 42):
    """Split au niveau des interactions individuelles (pas des utilisateurs)."""
    df_shuffled = df.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    split_idx = int(len(df_shuffled) * (1 - test_ratio))
    return df_shuffled.iloc[:split_idx], df_shuffled.iloc[split_idx:]


def evaluate_precision_at_k(user_factors, item_factors, train_matrix,
                             test_df, user_id_to_idx, product_id_to_idx, k: int = 10):
    """
    Precision@k : pour chaque utilisateur du test set, calcule la proportion
    d'items recommandés qui font effectivement partie de ses interactions cachées.
    """
    test_by_user = {}
    for _, row in test_df.iterrows():
        uid = row["user_id"]
        pid = row["product_id"]
        if uid in user_id_to_idx and pid in product_id_to_idx:
            test_by_user.setdefault(user_id_to_idx[uid], []).append(product_id_to_idx[pid])

    hits, total = 0, 0
    for user_idx, true_item_idxs in test_by_user.items():
        scores = user_factors[user_idx] @ item_factors.T
        seen = train_matrix[user_idx].indices
        scores[seen] = -np.inf
        top_k = set(np.argsort(scores)[::-1][:k])
        hits += len(top_k & set(true_item_idxs))
        total += len(true_item_idxs)

    return hits / total if total > 0 else 0.0


def evaluate_popularity_baseline(train_matrix, test_df, user_id_to_idx, product_id_to_idx, k: int = 10):
    """Baseline naïve : recommander systématiquement les k produits les plus populaires."""
    popularity = np.asarray(train_matrix.sum(axis=0)).flatten()
    top_popular = set(np.argsort(popularity)[::-1][:k])

    test_by_user = {}
    for _, row in test_df.iterrows():
        uid, pid = row["user_id"], row["product_id"]
        if uid in user_id_to_idx and pid in product_id_to_idx:
            test_by_user.setdefault(user_id_to_idx[uid], []).append(product_id_to_idx[pid])

    hits = sum(len(top_popular & set(items)) for items in test_by_user.values())
    total = sum(len(items) for items in test_by_user.values())
    return hits / total if total > 0 else 0.0


def run_training(n_components: int = 20, top_k: int = 10, test_ratio: float = 0.2):
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)

    print("[1/5] Chargement des interactions depuis la base...")
    df = load_interactions()
    print(f"   -> {len(df)} interactions uniques (user, produit)")
    print(f"   -> {df['user_id'].nunique()} utilisateurs, {df['product_id'].nunique()} produits")

    interactions_per_user = df.groupby("user_id").size()
    avg_interactions = interactions_per_user.mean()
    pct_single_interaction = (interactions_per_user == 1).mean() * 100
    print(f"   -> moyenne d'interactions/utilisateur : {avg_interactions:.2f}")
    print(f"   -> {pct_single_interaction:.1f}% des utilisateurs n'ont qu'UNE seule interaction "
          f"(limite structurelle connue du CF user-based sur ce type de dataset)")

    print("[2/5] Split train/test...")
    train_df, test_df = train_test_split_interactions(df, test_ratio=test_ratio)
    print(f"   -> {len(train_df)} interactions train, {len(test_df)} interactions test")

    print("[3/5] Construction de la matrice sparse et entraînement SVD...")
    train_matrix, user_id_to_idx, idx_to_product_id = build_sparse_matrix(train_df)
    product_id_to_idx = {pid: i for i, pid in idx_to_product_id.items()}

    svd = TruncatedSVD(n_components=n_components, random_state=42)
    user_factors = svd.fit_transform(train_matrix)
    item_factors = svd.components_.T
    print(f"   -> variance expliquée cumulée : {svd.explained_variance_ratio_.sum():.3f}")

    print("[4/5] Évaluation offline...")
    precision_cf = evaluate_precision_at_k(
        user_factors, item_factors, train_matrix, test_df, user_id_to_idx, product_id_to_idx, k=top_k
    )
    precision_pop = evaluate_popularity_baseline(
        train_matrix, test_df, user_id_to_idx, product_id_to_idx, k=top_k
    )
    print(f"   -> Precision@{top_k} collaborative filtering : {precision_cf:.4f}")
    print(f"   -> Precision@{top_k} baseline popularité      : {precision_pop:.4f}")
    if precision_pop > 0:
        print(f"   -> Amélioration relative : {(precision_cf / precision_pop - 1) * 100:+.1f}%")

    print("[5/5] Sauvegarde des artefacts...")
    popularity_scores = np.asarray(train_matrix.sum(axis=0)).flatten()
    popularity_ranking = [idx_to_product_id[i] for i in np.argsort(popularity_scores)[::-1]]

    joblib.dump(user_factors, os.path.join(ARTIFACTS_DIR, "user_factors.joblib"))
    joblib.dump(item_factors, os.path.join(ARTIFACTS_DIR, "item_factors.joblib"))
    joblib.dump(user_id_to_idx, os.path.join(ARTIFACTS_DIR, "user_id_to_idx.joblib"))
    joblib.dump(idx_to_product_id, os.path.join(ARTIFACTS_DIR, "idx_to_product_id.joblib"))
    joblib.dump(train_matrix, os.path.join(ARTIFACTS_DIR, "train_matrix.joblib"))
    joblib.dump(popularity_ranking, os.path.join(ARTIFACTS_DIR, "popularity_ranking.joblib"))

    metrics = {
        "n_components": n_components,
        "precision_at_k": precision_cf,
        "precision_at_k_baseline": precision_pop,
        "n_users": len(user_id_to_idx),
        "n_products": len(idx_to_product_id),
        "n_train_interactions": len(train_df),
        "n_test_interactions": len(test_df),
    }
    joblib.dump(metrics, os.path.join(ARTIFACTS_DIR, "metrics.joblib"))

    print(f"\nTerminé. Artefacts sauvegardés dans {ARTIFACTS_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-components", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--test-ratio", type=float, default=0.2)
    args = parser.parse_args()

    run_training(args.n_components, args.top_k, args.test_ratio)
