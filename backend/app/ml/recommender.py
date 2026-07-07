"""
Sert les recommandations à partir des artefacts entraînés par train_recommender.py.

Stratégie hybride :
- Si l'utilisateur a un historique d'interactions (présent dans les artefacts CF)
  -> recommandations personnalisées par factorisation matricielle.
- Sinon (nouvel utilisateur, cold-start)
  -> fallback sur les produits les plus populaires.

Les artefacts sont chargés une seule fois en mémoire au démarrage de l'API
(voir get_recommender(), utilisant un singleton simple).
"""

import os
from typing import List, Optional

import joblib
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

ARTIFACTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")


class Recommender:
    def __init__(self):
        self.user_factors = None
        self.item_factors = None
        self.user_id_to_idx = None
        self.idx_to_product_id = None
        self.product_id_to_idx = None
        self.train_matrix = None
        self.popularity_ranking: List[str] = []
        self.is_loaded = False

    def load(self):
        try:
            self.user_factors = joblib.load(os.path.join(ARTIFACTS_DIR, "user_factors.joblib"))
            self.item_factors = joblib.load(os.path.join(ARTIFACTS_DIR, "item_factors.joblib"))
            self.user_id_to_idx = joblib.load(os.path.join(ARTIFACTS_DIR, "user_id_to_idx.joblib"))
            self.idx_to_product_id = joblib.load(os.path.join(ARTIFACTS_DIR, "idx_to_product_id.joblib"))
            self.product_id_to_idx = {pid: i for i, pid in self.idx_to_product_id.items()}
            self.train_matrix = joblib.load(os.path.join(ARTIFACTS_DIR, "train_matrix.joblib"))
            self.popularity_ranking = joblib.load(os.path.join(ARTIFACTS_DIR, "popularity_ranking.joblib"))
            self.is_loaded = True
        except FileNotFoundError:
            # Les artefacts n'ont pas encore été générés (avant le premier `train_recommender.py`).
            # L'API reste fonctionnelle : on retombera systématiquement sur le fallback popularité,
            # mais celui-ci sera vide tant qu'aucun entraînement n'a eu lieu.
            self.is_loaded = False

    def recommend(self, user_id: Optional[str], top_k: int = 10) -> List[str]:
        """Retourne une liste d'UUID produits recommandés, du plus au moins pertinent."""
        if not self.is_loaded:
            return []

        if user_id is not None and user_id in self.user_id_to_idx:
            return self._recommend_personalized(user_id, top_k)

        return self._recommend_popular(top_k)

    def _recommend_personalized(self, user_id: str, top_k: int) -> List[str]:
        user_idx = self.user_id_to_idx[user_id]
        scores = self.user_factors[user_idx] @ self.item_factors.T

        seen_indices = self.train_matrix[user_idx].indices
        scores[seen_indices] = -np.inf

        top_indices = np.argsort(scores)[::-1][:top_k]
        return [self.idx_to_product_id[i] for i in top_indices]

    def _recommend_popular(self, top_k: int) -> List[str]:
        return self.popularity_ranking[:top_k]

    def similar_products(self, product_id: str, top_k: int = 10) -> List[str]:
        """
        Collaborative filtering item-based : retourne les produits les plus proches
        d'un produit donné dans l'espace latent (mesuré par similarité cosinus).

        Contrairement à recommend(), cette approche ne dépend PAS de la richesse de
        l'historique d'un utilisateur donné -- elle ne requiert qu'un signal de
        co-occurrence entre produits à travers TOUS les utilisateurs. C'est pourquoi
        elle reste pertinente même quand la majorité des utilisateurs n'ont qu'une
        seule interaction (cas du dataset Olist, cf. évaluation du modèle user-based).
        """
        if not self.is_loaded or product_id not in self.product_id_to_idx:
            return []

        item_idx = self.product_id_to_idx[product_id]
        sims = cosine_similarity(
            self.item_factors[item_idx].reshape(1, -1), self.item_factors
        )[0]
        sims[item_idx] = -np.inf

        top_indices = np.argsort(sims)[::-1][:top_k]
        return [self.idx_to_product_id[i] for i in top_indices]


# Singleton chargé une fois au démarrage de l'app (voir app/main.py)
_recommender_instance: Optional[Recommender] = None


def get_recommender() -> Recommender:
    global _recommender_instance
    if _recommender_instance is None:
        _recommender_instance = Recommender()
        _recommender_instance.load()
    return _recommender_instance
