"""
Point d'entrée de l'API FastAPI.
Lancement local : uvicorn app.main:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, products, stocks, recommendations, orders, dashboard

app = FastAPI(
    title="Plateforme E-commerce Intelligente — API",
    description="Catalogue produits, gestion des stocks et moteur de recommandation "
                 "pour petits e-commerçants marocains/africains (PFE).",
    version="0.1.0",
)

# CORS ouvert en développement (à restreindre en production au domaine du frontend).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(stocks.router)
app.include_router(recommendations.router)
app.include_router(orders.router)
app.include_router(dashboard.router)


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}
