"""
Gestionnaire de connexions WebSocket pour la diffusion temps réel des
changements de stock (achat, réapprovisionnement, ajustement manuel).

Point technique : nos routes FastAPI qui modifient le stock sont synchrones
(`def`, pas `async def`) car elles utilisent SQLAlchemy en mode synchrone.
Pour diffuser un message vers les WebSockets (qui sont, eux, asynchrones)
depuis ce contexte synchrone, on planifie la diffusion sur la boucle asyncio
principale via `asyncio.run_coroutine_threadsafe` -- pattern standard pour
faire communiquer du code sync et async dans la même application.
"""

import asyncio
from typing import Optional

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Appelé une fois au démarrage de l'app pour mémoriser la boucle asyncio active."""
        self.loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def _broadcast(self, message: dict) -> None:
        still_connected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
                still_connected.append(connection)
            except Exception:
                pass  # connexion fermée/cassée : on ne la garde pas dans la liste
        self.active_connections = still_connected

    def broadcast_from_sync(self, message: dict) -> None:
        """
        Point d'entrée utilisé par les routes synchrones (ex: création de
        commande, ajustement de stock) pour déclencher une diffusion sans
        bloquer ni nécessiter que la route elle-même soit async.
        """
        if self.loop is None:
            return  # pas encore démarré (ex: appelé depuis un test sans app.startup) -> no-op silencieux
        asyncio.run_coroutine_threadsafe(self._broadcast(message), self.loop)


manager = ConnectionManager()
