from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg2://pfe_user:pfe_password@localhost:5432/pfe_ecommerce"
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET: str = "change_me_in_prod"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    STRIPE_SECRET_KEY: str = "sk_test_placeholder"
    LOW_STOCK_THRESHOLD: int = 5

    class Config:
        env_file = ".env"


settings = Settings()
