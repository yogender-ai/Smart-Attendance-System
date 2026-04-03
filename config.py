import os
from cryptography.fernet import Fernet

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "smartface_super_secret_key_change_in_prod")
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

    # Database — auto-detect PostgreSQL (Neon) vs SQLite
    DATABASE_URL = os.environ.get("DATABASE_URL")  # Neon PostgreSQL URL
    DATABASE_URI = os.path.join(BASE_DIR, "database", "smartface.db")  # SQLite fallback
    USE_POSTGRES = bool(DATABASE_URL)

    # Mail Config
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_USERNAME", "noreply@smartface.local")

    # Face Data Encryption
    FACE_ENCRYPTION_KEY = os.environ.get("FACE_ENCRYPTION_KEY")
    if not FACE_ENCRYPTION_KEY:
        # Auto-generate for development (in production, set via env var)
        _key_file = os.path.join(BASE_DIR, ".face_key")
        if os.path.exists(_key_file):
            with open(_key_file, 'rb') as f:
                FACE_ENCRYPTION_KEY = f.read()
        else:
            FACE_ENCRYPTION_KEY = Fernet.generate_key()
            with open(_key_file, 'wb') as f:
                f.write(FACE_ENCRYPTION_KEY)
    elif isinstance(FACE_ENCRYPTION_KEY, str):
        FACE_ENCRYPTION_KEY = FACE_ENCRYPTION_KEY.encode()

    # Upload Config
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "profiles")
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # 2MB max upload

    # Scheduler
    ABSENTEE_EMAIL_HOUR = int(os.environ.get("ABSENTEE_EMAIL_HOUR", "18"))
    ABSENTEE_EMAIL_MINUTE = int(os.environ.get("ABSENTEE_EMAIL_MINUTE", "0"))
