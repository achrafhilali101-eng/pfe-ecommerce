"""
Point d'entrée de l'API FastAPI.
Lancement local : uvicorn app.main:app --reload
"""

import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, products, stocks, recommendations, orders, dashboard
from app.ws_manager import manager

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


@app.on_event("startup")
async def on_startup():
    # Mémorise la boucle asyncio active pour permettre aux routes synchrones
    # de déclencher des diffusions WebSocket (voir app/ws_manager.py).
    manager.set_loop(asyncio.get_event_loop())


@app.websocket("/ws/stock")
async def stock_websocket(websocket: WebSocket):
    """
    Diffuse en temps réel les changements de stock (achat, réassort, ajustement).
    Le frontend s'y connecte pour mettre à jour les jauges de stock affichées
    sans avoir besoin de recharger la page.
    """
    await manager.connect(websocket)
    try:
        while True:
            # On n'attend aucune donnée du client -- juste garder la connexion
            # ouverte. `receive_text` lève WebSocketDisconnect à la fermeture.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}
