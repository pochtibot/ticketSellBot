from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ticketbot"

    # Telegram
    telegram_token: Optional[str] = None

    # VK
    vk_token: Optional[str] = None
    vk_group_id: Optional[str] = None
    vk_app_id: Optional[int] = None      # App ID VK Mini App (для проверки sign)
    vk_secret_key: Optional[str] = None  # Секретный ключ VK приложения (для подписи launch params)
    vk_token_encryption_key: Optional[str] = None  # Fernet-ключ для шифрования community token VK-групп
    vk_pay_enabled: bool = False
    vk_pay_merchant_id: Optional[str] = None
    vk_pay_client_id: Optional[str] = None
    vk_pay_app_secure_key: Optional[str] = None
    vk_pay_merchant_private_key: Optional[str] = None
    vk_pay_notification_public_key: Optional[str] = None
    # MAX
    max_token: Optional[str] = None

    # Admin
    admin_telegram_ids: str = ""  # через запятую: "123456,789012"

    # Web / Mini App
    # Разрешить X-Skip-Auth (обход аутентификации) — ТОЛЬКО для dev/тестов.
    # По умолчанию False: на проде заголовок X-Skip-Auth игнорируется (401).
    allow_skip_auth: bool = False
    # Per-IP rate limiting (запросов в минуту); 0 = выключено.
    rate_limit_per_minute: int = 120
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    webapp_url: str = ""  # Публичный HTTPS URL Mini App (для WebAppInfo в кнопках)

    # App
    debug: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
