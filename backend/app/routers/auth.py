"""
Endpoints d'authentification : inscription et connexion.

POST /auth/register  -> crée un compte (acheteur ou vendeur) et retourne un token
POST /auth/login     -> vérifie les identifiants et retourne un token
GET  /auth/me        -> retourne l'utilisateur courant (à partir du token)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas
from app.security import hash_password, verify_password, create_access_token
from app.dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=schemas.Token, status_code=status.HTTP_201_CREATED)
def register(payload: schemas.UserRegister, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cet email est déjà utilisé.")

    user = models.User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=models.UserRole(payload.role),
    )
    db.add(user)
    db.flush()

    # Si l'inscription se fait en tant que vendeur, on crée directement son profil.
    if user.role == models.UserRole.SELLER:
        seller_profile = models.Seller(
            user_id=user.id,
            company_name=payload.full_name or f"Boutique de {payload.email.split('@')[0]}",
        )
        db.add(seller_profile)

    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": user.id, "role": user.role.value})
    return schemas.Token(access_token=token)


@router.post("/login", response_model=schemas.Token)
def login(payload: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email).first()

    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect.",
        )

    token = create_access_token({"sub": user.id, "role": user.role.value})
    return schemas.Token(access_token=token)


@router.get("/me", response_model=schemas.UserOut)
def get_me(current_user: models.User = Depends(get_current_user)):
    return current_user
