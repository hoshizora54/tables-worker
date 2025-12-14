import os
from dotenv import load_dotenv


load_dotenv()


class Settings:
    def __init__(self) -> None:
        # Core
        self.DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")

        # Flags
        self.LLM_ONLY_SQL = os.getenv("LLM_ONLY_SQL", "0") in ("1", "true", "True", "yes", "YES")

        self.POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
        self.POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
        self.POSTGRES_DB = os.getenv("POSTGRES_DB", "postgres")
        self.POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
        self.POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "123")
        if self.DATABASE_URL == "" or self.DATABASE_URL.startswith("postgresql://auto"):
            self.DATABASE_URL = (
                f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )

        # LLM / OpenRouter
        self.LLM_PROVIDER = os.getenv("LLM_PROVIDER", "yandex")

        # Yandex GPT
        self.YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "")
        self.YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "")
        self.YANDEX_LLM_MODEL = os.getenv("YANDEX_LLM_MODEL", "yandexgpt-lite")
        self.YANDEX_COMPLETION_URL = os.getenv(
            "YANDEX_COMPLETION_URL",
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
        )

        # CORS
        self.CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

        # Derived
        self._ensure_data_dir()

    @property
    def DB_DIALECT(self) -> str:
        url = self.DATABASE_URL.lower()
        if url.startswith("postgresql"):
            return "postgresql"
        if url.startswith("sqlite"):
            return "sqlite"
        return "sql"

    @staticmethod
    def _ensure_data_dir() -> None:
        # Ensure ./data exists for SQLite path
        data_dir = os.path.join(os.getcwd(), "data")
        try:
            os.makedirs(data_dir, exist_ok=True)
        except Exception:
            pass


settings = Settings()


