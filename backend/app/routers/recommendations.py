"""
Endpoint de recommandation.

GET /recommendations/{user_id}?top_k=10
    -> liste de produits recommandés pour cet utilisateur (personnalisé si historique
       disponible, sinon fallback popularité — cold-start).

GET /recommendations
    -> sans utilisateur (visiteur anonyme) : produits les plus populaires.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app import models, schemas
from app.ml.recommender import get_recommender

router = APIRouter(prefix="/recommendations", tags=["recommandations"])


def _products_from_ids(db: Session, product_ids: list[str]) -> list[models.Product]:
    """Récupère les produits en base tout en préservant l'ordre de pertinence du modèle."""
    if not product_ids:
        return []

    products = (
        db.query(models.Product)
        .options(joinedload(models.Product.category))
        .filter(models.Product.id.in_(product_ids))
        .all()
    )
    products_by_id = {p.id: p for p in products}
    # Reconstruit l'ordre original (le modèle classe du plus au moins pertinent),
    # en ignorant silencieusement un id qui n'existerait plus (produit supprimé/inactif).
    return [products_by_id[pid] for pid in product_ids if pid in products_by_id]


@router.get("", response_model=list[schemas.ProductListOut])
def recommend_anonymous(
    db: Session = Depends(get_db),
    top_k: int = Query(default=10, ge=1, le=50),
):
    """Recommandations pour un visiteur non authentifié : produits populaires."""
    recommender = get_recommender()
    product_ids = recommender.recommend(user_id=None, top_k=top_k)
    return _products_from_ids(db, product_ids)


@router.get("/{user_id}", response_model=list[schemas.ProductListOut])
def recommend_for_user(
    user_id: str,
    db: Session = Depends(get_db),
    top_k: int = Query(default=10, ge=1, le=50),
):
    """
    Recommandations personnalisées pour un utilisateur donné.
    Si l'utilisateur n'a pas d'historique connu du modèle (cold-start),
    retombe automatiquement sur les produits populaires.
    """
    recommender = get_recommender()
    product_ids = recommender.recommend(user_id=user_id, top_k=top_k)
    return _products_from_ids(db, product_ids)


@router.get("/similar/{product_id}", response_model=list[schemas.ProductListOut])
def similar_products(
    product_id: str,
    db: Session = Depends(get_db),
    top_k: int = Query(default=10, ge=1, le=50),
):
    """
    Collaborative filtering item-based : produits similaires à celui consulté
    ("les clients ayant acheté ceci ont aussi acheté..."). À utiliser sur la page
    détail produit -- ne dépend pas de la richesse de l'historique utilisateur.
    """
    recommender = get_recommender()
    product_ids = recommender.similar_products(product_id, top_k=top_k)
    return _products_from_ids(db, product_ids)
