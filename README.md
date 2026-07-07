# Plateforme E-commerce Intelligente — PFE

Moteur de recommandation personnalisé + gestion dynamique des stocks en temps réel,
pour petits e-commerçants marocains/africains.

## État d'avancement (Jour 1-2)

- [x] Structure du projet (backend / frontend / data)
- [x] Docker Compose (Postgres + Redis + API)
- [x] Schéma de base de données (SQLAlchemy models — 10 tables)
- [x] Setup Alembic (migrations)
- [x] Script ETL Olist -> PostgreSQL (`backend/scripts/etl_olist.py`) — **testé et validé**
- [x] Script génération données synthétiques Faker (`backend/scripts/generate_fake_data.py`) — **logique testée**
- [ ] API FastAPI (catalogue, auth, stocks)
- [ ] Moteur de recommandation
- [ ] Frontend React
- [ ] Dashboard vendeur
- [ ] WebSocket temps réel
- [ ] CI/CD

## Setup local

### 1. Prérequis
- Docker + Docker Compose
- Python 3.11+ (si tu veux lancer les scripts hors conteneur)

### 2. Télécharger le dataset Olist

Télécharge sur Kaggle : **"Brazilian E-Commerce Public Dataset by Olist"**
https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce

Place les fichiers CSV dans `data/raw/` :
```
data/raw/
├── olist_sellers_dataset.csv
├── olist_products_dataset.csv
├── olist_orders_dataset.csv
├── olist_order_items_dataset.csv
├── olist_order_reviews_dataset.csv
├── olist_customers_dataset.csv
└── product_category_name_translation.csv
```

### 3. Lancer l'infrastructure

```bash
docker-compose up -d postgres redis
```

### 4. Installer les dépendances backend (en local, pour lancer les scripts)

```bash
cd backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 5. Créer les tables (migration initiale)

```bash
cd backend
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

### 6. Importer les données Olist

```bash
# Test sans DB d'abord (génère des CSV dans data/processed/ pour vérifier la logique)
python scripts/etl_olist.py --raw-dir ../data/raw --out-dir ../data/processed

# Une fois validé, charger réellement en base
python scripts/etl_olist.py --raw-dir ../data/raw --out-dir ../data/processed --load-db
```

### 7. Générer les données complémentaires (comptes démo, historique stock)

```bash
python scripts/generate_fake_data.py --load-db
```

Cela crée :
- 15 comptes acheteurs : `buyer1@demo.com` ... `buyer15@demo.com` (mdp: `Demo1234!`)
- 3 comptes vendeurs : `seller1@demo.com` ... `seller3@demo.com` (mdp: `Demo1234!`)
- 500 interactions de navigation simulées (vues, ajouts panier)
- Historique de mouvements de stock sur 30 jours

### 8. Lancer l'API

```bash
docker-compose up -d api
# ou en local :
uvicorn app.main:app --reload
```

## Schéma de données

Voir `backend/app/models.py` pour le détail. Tables principales :

| Table | Rôle |
|---|---|
| `users` | Acheteurs / vendeurs / admin |
| `sellers` | Profil vendeur (lié à Olist via `olist_seller_id`) |
| `products` | Catalogue (lié à Olist via `olist_product_id`) |
| `categories` | Catégories produits |
| `stocks` | **Source de vérité** du stock courant (temps réel) |
| `stock_movements` | Historique append-only des mouvements (audit) |
| `orders` / `order_items` | Commandes |
| `interactions` | Table clé pour la reco : vues, achats, notes, ajouts panier |

## Pourquoi ce design

- **`interactions` séparée de `orders`** : permet de capter des signaux faibles (vues,
  ajouts panier) en plus des achats, ce qui réduit le problème de cold-start du
  collaborative filtering pur.
- **`stock_movements` en append-only** : traçabilité complète, permet de reconstruire
  l'état du stock à tout instant et d'alimenter les graphiques du dashboard vendeur.
- **Prix produit calculé depuis `order_items`** : Olist ne fournit pas de prix catalogue
  direct, seulement des prix de transaction. Le prix moyen observé est utilisé.
- **Stock simulé** : Olist étant un dataset historique de marketplace, il n'y a pas de
  notion de stock. On simule un stock initial réaliste par produit avec `numpy`.

## Prochaine étape (Jour 3)

API FastAPI : authentification JWT + endpoints catalogue produits.
