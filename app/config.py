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
# How many independent extractions to run per request. Fields must agree
# across a majority of samples to be returned; 1 disables the check.
EXTRACTION_SAMPLES: str = os.getenv("EXTRACTION_SAMPLES", "3")
MAX_EXTRACTION_SAMPLES: int = 5
# How many of the ten public fields may be unreadable before the whole
# extraction is rejected as an unusable photo. At eight or more missing
# fields the result is too sparse to be worth returning, so the caller is
# asked for a better picture instead.
MAX_UNREADABLE_FIELDS: int = 7
# Include the actual extracted values in the consensus log lines. Off by
# default: those values are personal data (name, NID number, address) and
# normal operation logs metadata only. For local debugging of extraction
# quality, never for a deployed environment.
CONSENSUS_LOG_VALUES: str = os.getenv("CONSENSUS_LOG_VALUES", "false")

_TRUTHY = {"1", "true", "yes", "on"}


def consensus_log_values() -> bool:
    """Whether consensus logging may include extracted field values.

    Anything other than an explicit truthy value keeps the values hidden, so
    a typo fails safe towards not logging personal data.
    """
    return CONSENSUS_LOG_VALUES.strip().lower() in _TRUTHY


def extraction_samples() -> int:
    """Resolve the configured sample count, read fresh on each call.

    Falls back to 3 if the value is not a number, and clamps to 1..5 so a
    misconfiguration cannot issue an unbounded number of API calls per
    request.
    """
    try:
        count = int(EXTRACTION_SAMPLES)
    except (TypeError, ValueError):
        return 3
    return max(1, min(count, MAX_EXTRACTION_SAMPLES))
