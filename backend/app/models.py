"""
Schéma de base de données de la plateforme e-commerce.

Tables principales :
- users            : acheteurs et vendeurs (rôle)
- sellers          : profil vendeur (issu d'Olist ou créé via l'app)
- categories       : catégories de produits
- products         : catalogue produits
- stocks           : niveau de stock courant par produit (source de vérité temps réel)
- stock_movements  : historique des mouvements de stock (audit + reconstruction)
- orders           : commandes passées par les acheteurs
- order_items      : lignes de commande (produit, quantité, prix)
- interactions     : événements user-produit utilisés pour le moteur de recommandation
                      (vue, achat, note) -> alimente la matrice de collaborative filtering
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Integer, Float, ForeignKey, DateTime, Enum, Text, Boolean, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class UserRole(str, enum.Enum):
    BUYER = "buyer"
    SELLER = "seller"
    ADMIN = "admin"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class InteractionType(str, enum.Enum):
    VIEW = "view"
    PURCHASE = "purchase"
    RATING = "rating"
    ADD_TO_CART = "add_to_cart"


class StockMovementType(str, enum.Enum):
    RESTOCK = "restock"
    SALE = "sale"
    ADJUSTMENT = "adjustment"
    RETURN = "return"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    role = Column(Enum(UserRole), default=UserRole.BUYER, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    seller_profile = relationship("Seller", back_populates="user", uselist=False)
    orders = relationship("Order", back_populates="buyer")
    interactions = relationship("Interaction", back_populates="user")


class Seller(Base):
    __tablename__ = "sellers"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    olist_seller_id = Column(String(64), unique=True, nullable=True, index=True)  # traçabilité dataset Olist
    company_name = Column(String(255), nullable=False)
    city = Column(String(120), nullable=True)
    state = Column(String(10), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="seller_profile")
    products = relationship("Product", back_populates="seller")


class Category(Base):
    __tablename__ = "categories"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String(150), unique=True, nullable=False)
    slug = Column(String(150), unique=True, nullable=False, index=True)

    products = relationship("Product", back_populates="category")


class Product(Base):
    __tablename__ = "products"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    olist_product_id = Column(String(64), unique=True, nullable=True, index=True)  # traçabilité dataset Olist
    seller_id = Column(UUID(as_uuid=False), ForeignKey("sellers.id"), nullable=False)
    category_id = Column(UUID(as_uuid=False), ForeignKey("categories.id"), nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=False)
    image_url = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    seller = relationship("Seller", back_populates="products")
    category = relationship("Category", back_populates="products")
    stock = relationship("Stock", back_populates="product", uselist=False)
    order_items = relationship("OrderItem", back_populates="product")
    interactions = relationship("Interaction", back_populates="product")


class Stock(Base):
    """Source de vérité du stock courant. Mise à jour de façon atomique (transaction SQL)."""
    __tablename__ = "stocks"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id"), unique=True, nullable=False)
    quantity = Column(Integer, nullable=False, default=0)
    low_stock_threshold = Column(Integer, nullable=False, default=5)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    product = relationship("Product", back_populates="stock")


class StockMovement(Base):
    """Historique append-only des mouvements de stock (audit + rejouabilité)."""
    __tablename__ = "stock_movements"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id"), nullable=False)
    movement_type = Column(Enum(StockMovementType), nullable=False)
    quantity_delta = Column(Integer, nullable=False)  # positif = entrée, négatif = sortie
    reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Order(Base):
    __tablename__ = "orders"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    buyer_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING, nullable=False)
    total_amount = Column(Float, nullable=False, default=0.0)
    stripe_payment_intent_id = Column(String(255), nullable=True)
    shipping_address = Column(String(500), nullable=True)
    shipping_phone = Column(String(30), nullable=True)
    shipping_email = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    buyer = relationship("User", back_populates="orders")
    items = relationship("OrderItem", back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    order_id = Column(UUID(as_uuid=False), ForeignKey("orders.id"), nullable=False)
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")

    @property
    def product_name(self):
        return self.product.name if self.product else None


class Interaction(Base):
    """
    Table clé pour le moteur de recommandation.
    Chaque ligne = un signal implicite ou explicite user-produit.
    Sert à construire la matrice user-item pour le collaborative filtering.
    """
    __tablename__ = "interactions"
    __table_args__ = (
        UniqueConstraint("user_id", "product_id", "interaction_type", name="uq_user_product_interaction"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id"), nullable=False)
    interaction_type = Column(Enum(InteractionType), nullable=False)
    rating = Column(Float, nullable=True)  # utilisé si interaction_type == RATING (1-5, issu d'Olist reviews)
    weight = Column(Float, default=1.0)  # poids implicite (ex: view=1, cart=2, purchase=5)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="interactions")
    product = relationship("Product", back_populates="interactions")