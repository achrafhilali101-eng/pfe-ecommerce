"""
Dépendances FastAPI réutilisables :
- get_current_user : extrait et valide l'utilisateur depuis le header Authorization
- require_role : restreint l'accès à un rôle donné (ex: seller)
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.database import get_db
from app.security import decode_access_token
from app import models

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Identifiants invalides ou expirés",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise credentials_exception

    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise credentials_exception

    return user


def require_role(*allowed_roles: str):
    """
    Factory de dépendance : require_role("seller") -> 403 si l'utilisateur
    courant n'a pas ce rôle. Usage : Depends(require_role("seller"))
    """

    def role_checker(current_user: models.User = Depends(get_current_user)) -> models.User:
        if current_user.role.value not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Accès réservé aux rôles : {', '.join(allowed_roles)}",
            )
        return current_user

    return role_checker