"""
Schémas Pydantic — définissent la forme des données échangées avec l'API
(séparés des modèles SQLAlchemy qui définissent, eux, le schéma de la base).
"""

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ---------- Auth ----------

class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    full_name: Optional[str] = None
    role: str = Field(default="buyer", pattern="^(buyer|seller)$")


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    full_name: Optional[str] = None
    role: str
    created_at: datetime


# ---------- Catalogue ----------

class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str


class StockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    quantity: int
    low_stock_threshold: int


class SellerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_name: str
    city: Optional[str] = None
    state: Optional[str] = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: Optional[str] = None
    price: float
    image_url: Optional[str] = None
    is_active: bool
    category: Optional[CategoryOut] = None
    seller: Optional[SellerOut] = None
    stock: Optional[StockOut] = None


class ProductListOut(BaseModel):
    """Version allégée pour les listes (évite de surcharger la réponse)."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    price: float
    image_url: Optional[str] = None
    category: Optional[CategoryOut] = None


class PaginatedProducts(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[ProductListOut]


class ProductCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: float = Field(gt=0)
    category_id: Optional[str] = None
    image_url: Optional[str] = None
    initial_stock: int = Field(default=0, ge=0)


class ProductUpdate(BaseModel):
    """Tous les champs sont optionnels : seuls ceux fournis sont modifiés (PATCH partiel)."""
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = Field(default=None, gt=0)
    category_id: Optional[str] = None
    image_url: Optional[str] = None
    is_active: Optional[bool] = None


# ---------- Stocks ----------

class StockAdjust(BaseModel):
    """Utilisé par le vendeur pour ajuster manuellement un stock (restock, correction)."""
    quantity_delta: int
    reason: Optional[str] = None


# ---------- Commandes ----------

class OrderItemCreate(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)


class OrderCreate(BaseModel):
    items: List[OrderItemCreate]


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: str
    product_name: Optional[str] = None
    quantity: int
    unit_price: float


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    total_amount: float
    created_at: datetime
    items: List[OrderItemOut] = []


# ---------- Dashboard vendeur ----------

class SellerProductOut(BaseModel):
    """Produit vu depuis le dashboard vendeur : inclut le stock, pas les relations lourdes."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    price: float
    is_active: bool
    image_url: Optional[str] = None
    category: Optional[CategoryOut] = None
    stock: Optional[StockOut] = None


class RevenuePoint(BaseModel):
    date: str
    revenue: float
    orders_count: int


class StockMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: str
    movement_type: str
    quantity_delta: int
    reason: Optional[str] = None
    created_at: datetime


class SellerDashboardSummary(BaseModel):
    total_products: int
    total_revenue: float
    total_orders: int
    low_stock_count: int
    revenue_last_30_days: List[RevenuePoint]
    recent_movements: List[StockMovementOut]
