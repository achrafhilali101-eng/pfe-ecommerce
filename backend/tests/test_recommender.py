"""
Tests du moteur de recommandation.

Deux niveaux de test :
- Tests unitaires sur la classe Recommender directement, avec des artefacts
  synthétiques (rapide, ne dépend pas de la base de données).
- Test d'intégration sur l'endpoint API, qui doit rester fonctionnel (200,
  liste vide) même si aucun modèle n'a encore été entraîné -- c'est le
  comportement attendu avant le premier lancement de train_recommender.py.
"""

import numpy as np
import joblib
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD

from app.ml.recommender import Recommender


def _build_fake_artifacts(tmp_path):
    """Construit un jeu d'artefacts synthétique et les sauvegarde dans tmp_path."""
    np.random.seed(1)
    n_users, n_items = 15, 12

    rows, cols, vals = [], [], []
    for u in range(n_users):
        for it in np.random.choice(n_items, size=4, replace=False):
            rows.append(u)
            cols.append(it)
            vals.append(1.0)
    matrix = csr_matrix((vals, (rows, cols)), shape=(n_users, n_items))

    svd = TruncatedSVD(n_components=5, random_state=1)
    user_factors = svd.fit_transform(matrix)
    item_factors = svd.components_.T

    user_id_to_idx = {f"user_{i}": i for i in range(n_users)}
    idx_to_product_id = {i: f"prod_{i}" for i in range(n_items)}
    popularity = np.asarray(matrix.sum(axis=0)).flatten()
    popularity_ranking = [idx_to_product_id[i] for i in np.argsort(popularity)[::-1]]

    joblib.dump(user_factors, tmp_path / "user_factors.joblib")
    joblib.dump(item_factors, tmp_path / "item_factors.joblib")
    joblib.dump(user_id_to_idx, tmp_path / "user_id_to_idx.joblib")
    joblib.dump(idx_to_product_id, tmp_path / "idx_to_product_id.joblib")
    joblib.dump(matrix, tmp_path / "train_matrix.joblib")
    joblib.dump(popularity_ranking, tmp_path / "popularity_ranking.joblib")

    return matrix, user_id_to_idx, idx_to_product_id


def _recommender_with_artifacts(tmp_path):
    _build_fake_artifacts(tmp_path)
    recommender = Recommender()
    import joblib as _joblib
    recommender.user_factors = _joblib.load(tmp_path / "user_factors.joblib")
    recommender.item_factors = _joblib.load(tmp_path / "item_factors.joblib")
    recommender.user_id_to_idx = _joblib.load(tmp_path / "user_id_to_idx.joblib")
    recommender.idx_to_product_id = _joblib.load(tmp_path / "idx_to_product_id.joblib")
    recommender.product_id_to_idx = {pid: i for i, pid in recommender.idx_to_product_id.items()}
    recommender.train_matrix = _joblib.load(tmp_path / "train_matrix.joblib")
    recommender.popularity_ranking = _joblib.load(tmp_path / "popularity_ranking.joblib")
    recommender.is_loaded = True
    return recommender


def test_recommender_not_loaded_returns_empty_list():
    recommender = Recommender()
    recommender.is_loaded = False
    assert recommender.recommend(user_id="user_0", top_k=5) == []
    assert recommender.similar_products("prod_0", top_k=5) == []


def test_recommender_personalized_excludes_already_seen_items(tmp_path):
    recommender = _recommender_with_artifacts(tmp_path)

    seen_indices = set(recommender.train_matrix[0].indices)
    recommendations = recommender.recommend(user_id="user_0", top_k=8)

    recommended_indices = {recommender.product_id_to_idx[pid] for pid in recommendations}
    assert recommended_indices.isdisjoint(seen_indices)


def test_recommender_falls_back_to_popularity_for_unknown_user(tmp_path):
    recommender = _recommender_with_artifacts(tmp_path)

    recommendations = recommender.recommend(user_id="utilisateur_jamais_vu", top_k=5)
    assert recommendations == recommender.popularity_ranking[:5]


def test_recommender_anonymous_user_gets_popularity(tmp_path):
    recommender = _recommender_with_artifacts(tmp_path)

    recommendations = recommender.recommend(user_id=None, top_k=3)
    assert recommendations == recommender.popularity_ranking[:3]


def test_similar_products_returns_empty_for_unknown_product(tmp_path):
    recommender = _recommender_with_artifacts(tmp_path)

    assert recommender.similar_products("produit_inexistant", top_k=5) == []


def test_similar_products_excludes_itself(tmp_path):
    recommender = _recommender_with_artifacts(tmp_path)

    similar = recommender.similar_products("prod_0", top_k=11)  # presque tous les items
    assert "prod_0" not in similar


def test_recommendations_endpoint_returns_valid_list(client):
    """
    Vérifie que l'endpoint répond toujours 200 avec une liste, que le modèle
    ait déjà été entraîné sur cette machine (cas réel en développement) ou non
    (cas d'une CI fraîche sans artefacts) -- le comportement ne doit jamais
    être une erreur 500 dans un cas comme dans l'autre.
    """
    response = client.get("/recommendations")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_recommendations_for_unknown_user_falls_back_gracefully(client):
    """
    Un identifiant utilisateur inconnu du modèle (ou aucun modèle entraîné du
    tout) doit systématiquement retomber sur le fallback popularité -- jamais
    une erreur.
    """
    response = client.get("/recommendations/un-utilisateur-qui-nexiste-pas")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
