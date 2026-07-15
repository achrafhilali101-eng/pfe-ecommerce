"""
Expérience de comparaison : applique le MÊME pipeline de collaborative filtering
(TruncatedSVD + évaluation Precision@k vs baseline popularité) que
app/ml/train_recommender.py, mais sur un second dataset réel indépendant :
Amazon Fine Food Reviews.

Objectif : démontrer que la méthode généralise à un dataset de nature différente
(avis alimentaires vs marketplace généraliste Olist), PAS d'intégrer ces produits
au catalogue du site -- ce script est un module de comparaison scientifique pour
le rapport de PFE, autonome et indépendant du reste de l'application.

Dataset attendu : "Reviews.csv" (Amazon Fine Food Reviews, Kaggle), colonnes :
    Id, ProductId, UserId, ProfileName, HelpfulnessNumerator,
    HelpfulnessDenominator, Score, Time, Summary, Text

Usage:
    python -m app.ml.amazon_comparison.train_amazon_recommender \
        --csv-path ../../../data/raw/Reviews.csv --n-components 20 --top-k 10
"""

import argparse
import os

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD


def load_reviews(csv_path: str, max_rows: int = None) -> pd.DataFrame:
    """
    Charge Reviews.csv et ne garde que les colonnes utiles à la reco.
    `max_rows` permet de limiter la taille pour un test rapide (le fichier
    complet fait ~500k lignes) -- mettre None pour tout charger.
    """
    df = pd.read_csv(csv_path, nrows=max_rows, usecols=["UserId", "ProductId", "Score"])
    df = df.rename(columns={"UserId": "user_id", "ProductId": "product_id", "Score": "rating"})
    df = df.dropna()
    # Une même personne peut avoir noté deux fois le même produit (rare) -> on moyenne.
    df = df.groupby(["user_id", "product_id"], as_index=False)["rating"].mean()
    return df


def build_sparse_matrix(df: pd.DataFrame):
    user_ids = df["user_id"].unique()
    product_ids = df["product_id"].unique()

    user_id_to_idx = {uid: i for i, uid in enumerate(user_ids)}
    product_id_to_idx = {pid: i for i, pid in enumerate(product_ids)}
    idx_to_product_id = {i: pid for pid, i in product_id_to_idx.items()}

    rows = df["user_id"].map(user_id_to_idx).values
    cols = df["product_id"].map(product_id_to_idx).values
    vals = df["rating"].values

    matrix = csr_matrix((vals, (rows, cols)), shape=(len(user_ids), len(product_ids)))
    return matrix, user_id_to_idx, idx_to_product_id


def train_test_split_interactions(df: pd.DataFrame, test_ratio: float = 0.2, seed: int = 42):
    df_shuffled = df.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    split_idx = int(len(df_shuffled) * (1 - test_ratio))
    return df_shuffled.iloc[:split_idx], df_shuffled.iloc[split_idx:]


def evaluate_precision_at_k(user_factors, item_factors, train_matrix,
                             test_df, user_id_to_idx, product_id_to_idx, k: int = 10):
    test_by_user = {}
    for _, row in test_df.iterrows():
        uid, pid = row["user_id"], row["product_id"]
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


def run_experiment(csv_path: str, n_components: int = 20, top_k: int = 10,
                    test_ratio: float = 0.2, max_rows: int = None):
    print("=== Expérience de comparaison : Amazon Fine Food Reviews ===\n")

    print("[1/5] Chargement des avis...")
    df = load_reviews(csv_path, max_rows=max_rows)
    print(f"   -> {len(df)} interactions uniques (user, produit)")
    print(f"   -> {df['user_id'].nunique()} utilisateurs, {df['product_id'].nunique()} produits")

    interactions_per_user = df.groupby("user_id").size()
    avg_interactions = interactions_per_user.mean()
    pct_single = (interactions_per_user == 1).mean() * 100
    print(f"   -> moyenne d'interactions/utilisateur : {avg_interactions:.2f}")
    print(f"   -> {pct_single:.1f}% des utilisateurs n'ont qu'UNE seule interaction")

    print("\n[2/5] Split train/test...")
    train_df, test_df = train_test_split_interactions(df, test_ratio=test_ratio)
    print(f"   -> {len(train_df)} interactions train, {len(test_df)} interactions test")

    print("\n[3/5] Construction de la matrice sparse et entraînement SVD...")
    train_matrix, user_id_to_idx, idx_to_product_id = build_sparse_matrix(train_df)
    product_id_to_idx = {pid: i for i, pid in idx_to_product_id.items()}

    svd = TruncatedSVD(n_components=n_components, random_state=42)
    user_factors = svd.fit_transform(train_matrix)
    item_factors = svd.components_.T
    print(f"   -> variance expliquée cumulée : {svd.explained_variance_ratio_.sum():.3f}")

    print("\n[4/5] Évaluation offline...")
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

    print("\n[5/5] Comparaison avec Olist (résultats déjà obtenus) :")
    print("   -> Olist  : ~1.07 interaction/utilisateur en moyenne -> CF ≈ baseline popularité")
    print(f"   -> Amazon : {avg_interactions:.2f} interactions/utilisateur en moyenne -> "
          f"{'CF surpasse nettement la baseline' if precision_cf > precision_pop * 1.1 else 'comportement similaire à Olist'}")
    print("\nCette comparaison illustre que la performance du CF user-based dépend "
          "directement de la densité d'interactions par utilisateur, pas seulement "
          "du volume de données brutes -- un même pipeline peut réussir ou échouer "
          "selon la structure du dataset.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv-path", default="../../../data/raw/Reviews.csv")
    parser.add_argument("--n-components", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--test-ratio", type=float, default=0.2)
    parser.add_argument("--max-rows", type=int, default=None,
                         help="Limiter le nombre de lignes chargées (le CSV complet fait ~500k lignes)")
    args = parser.parse_args()

    if not os.path.exists(args.csv_path):
        print(f"[ERREUR] Fichier introuvable : {args.csv_path}")
        print("Télécharge 'Amazon Fine Food Reviews' sur Kaggle et place Reviews.csv dans data/raw/")
    else:
        run_experiment(args.csv_path, args.n_components, args.top_k, args.test_ratio, args.max_rows)
