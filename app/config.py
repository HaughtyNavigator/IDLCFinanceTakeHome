"""Application configuration loaded from environment variables."""

import os

from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
MAX_IMAGE_DIMENSION: int = 1600
MIN_IMAGE_DIMENSION: int = 300
MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024
GEMINI_TIMEOUT_SECONDS: int = 60
GEMINI_RETRY_DELAY_SECONDS: float = 2.0
