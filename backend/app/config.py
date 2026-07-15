from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # NOTE: si un PostgreSQL natif tourne déjà sur le port 5432 de la machine hôte
    # (fréquent sur Windows), le docker-compose.yml expose Postgres sur 5433 à la place.
    # Adapter ce port si ton environnement diffère (vérifier avec `docker ps`).
    DATABASE_URL: str = "postgresql+psycopg2://pfe_user:pfe_password@localhost:5433/pfe_ecommerce"
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET: str = "change_me_in_prod"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    STRIPE_SECRET_KEY: str = "sk_test_placeholder"
    FRONTEND_URL: str = "http://localhost:5173"
    LOW_STOCK_THRESHOLD: int = 5

    class Config:
        env_file = ".env"


settings = Settings()
