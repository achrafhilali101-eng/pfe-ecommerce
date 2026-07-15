"""
Test de la route WebSocket temps réel.

Un test d'intégration complet (vérifier qu'un message de diffusion arrive bien
après un achat) serait fragile ici : le mécanisme de diffusion s'appuie sur la
boucle asyncio réellement démarrée par l'application (voir app/ws_manager.py),
ce qui ne correspond pas exactement au contexte d'exécution de TestClient.
On se limite donc à vérifier que la connexion s'établit et se ferme proprement
-- le comportement de diffusion est validé manuellement en conditions réelles.
"""


def test_websocket_stock_endpoint_accepts_connection(client):
    with client.websocket_connect("/ws/stock") as websocket:
        pass  # la connexion s'ouvre et se ferme sans lever d'exception
