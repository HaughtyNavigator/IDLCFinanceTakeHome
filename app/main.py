"""FastAPI application exposing the Bangladesh NID extraction API.

Only request metadata (method, path, status code, duration, error category)
is ever logged. Image bytes, extracted field values, and the Gemini API key
are never logged or included in error messages.
"""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from app import config
from app.gemini_client import (
    GeminiConfigurationError,
    GeminiServiceError,
    extract_nid_data,
)
from app.image_utils import ImageValidationError, validate_and_prepare
from app.schemas import ErrorResponse, GeminiNIDExtraction, NIDData

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("nid_extractor")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

UNREADABLE_MESSAGE = (
    "The images were not clear enough to read reliably. Too few fields could "
    "be extracted consistently. Please upload sharper, well-lit photos of the "
    "NID card and try again."
)

MISSING_KEY_MESSAGE = (
    "Server is not configured: GEMINI_API_KEY is not set. Set it in a .env "
    "file (see .env.example) or pass it to the container, then restart."
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Warn loudly at startup if the server has no Gemini API key.

    The app still starts so the container does not crash-loop under the
    Compose ``restart: unless-stopped`` policy; extraction requests then fail
    with a 503 that names the missing variable.
    """
    if not config.GEMINI_API_KEY:
        logger.critical(MISSING_KEY_MESSAGE)
    yield


app = FastAPI(
    title="Bangladesh NID Information Extractor",
    description=(
        "Upload the front and back images of a Bangladesh National ID card "
        "to extract structured, translated field data."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


class _TooLargeError(Exception):
    """Raised internally when the combined upload exceeds the size limit."""


def _error_category(status_code: int) -> str:
    """Classify a status code into a coarse error category for logging."""
    if status_code >= 500:
        return "server_error"
    if status_code >= 400:
        return "client_error"
    return "ok"


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log one line per request with only non-sensitive metadata."""
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "method=%s path=%s status_code=%s duration_ms=%.1f error_category=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        _error_category(response.status_code),
    )
    return response


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return a 422 with a field-specific message for missing uploads."""
    missing_fields = [
        str(error["loc"][-1])
        for error in exc.errors()
        if error.get("type") == "missing"
    ]
    if missing_fields:
        message = f"Missing required file field(s): {', '.join(missing_fields)}"
    else:
        message = "Invalid request."
    return JSONResponse(status_code=422, content={"error": message})


@app.exception_handler(ImageValidationError)
async def handle_image_validation_error(
    request: Request, exc: ImageValidationError
) -> JSONResponse:
    """Return a 400 with the user-facing image validation message."""
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(GeminiConfigurationError)
async def handle_gemini_configuration_error(
    request: Request, exc: GeminiConfigurationError
) -> JSONResponse:
    """Return a 503 naming the missing configuration variable.

    Registered separately from the GeminiServiceError handler so an operator
    error is not reported as a transient upstream outage. Only the variable
    name is disclosed, never its value.
    """
    logger.critical(MISSING_KEY_MESSAGE)
    return JSONResponse(status_code=503, content={"error": MISSING_KEY_MESSAGE})


@app.exception_handler(GeminiServiceError)
async def handle_gemini_service_error(
    request: Request, exc: GeminiServiceError
) -> JSONResponse:
    """Return a 502 for any AI service failure, without leaking details."""
    return JSONResponse(
        status_code=502,
        content={"error": "AI service temporarily unavailable, please retry."},
    )


@app.exception_handler(_TooLargeError)
async def handle_too_large_error(request: Request, exc: _TooLargeError) -> JSONResponse:
    """Return a 413 when the combined upload exceeds the size limit."""
    return JSONResponse(
        status_code=413,
        content={"error": "Request too large (maximum 10 MB total)."},
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Return a generic 500, logging only the exception class name."""
    logger.error("Unhandled exception: %s", type(exc).__name__)
    return JSONResponse(status_code=500, content={"error": "Internal server error."})


@app.get("/", include_in_schema=False)
async def serve_index() -> FileResponse:
    """Serve the static demo page."""
    return FileResponse(STATIC_DIR / "index.html")


def _read_and_check_size(front: UploadFile, back: UploadFile) -> tuple[bytes, bytes]:
    """Read both uploads and enforce the combined size limit.

    Raises:
        _TooLargeError: If the combined size exceeds the configured maximum.
    """
    front_bytes = front.file.read()
    back_bytes = back.file.read()
    if len(front_bytes) + len(back_bytes) > config.MAX_UPLOAD_BYTES:
        raise _TooLargeError()
    return front_bytes, back_bytes


def _resolve_extraction_outcome(extraction: GeminiNIDExtraction) -> NIDData:
    """Decide the response for a completed extraction.

    Raises:
        HTTPException: With a 422 status if the images are not a Bangladesh
            NID card, or if too many fields failed the agreement check.
    """
    if not extraction.is_nid:
        raise HTTPException(
            status_code=422,
            detail="The uploaded images do not appear to be a Bangladesh NID card.",
        )

    public_data = extraction.to_public()
    unreadable = sum(1 for value in public_data.model_dump().values() if not value)
    logger.info(
        "extraction outcome: %d unreadable field(s), limit %d",
        unreadable,
        config.MAX_UNREADABLE_FIELDS,
    )
    if unreadable > config.MAX_UNREADABLE_FIELDS:
        # A result this sparse is not worth returning: the samples disagreed
        # on most of the card, so the photo itself is the problem.
        # The model's own complaint, when it gave one, is useful context but
        # rarely tells the user what to do; the standard ask is appended so
        # the message always ends with the retry instruction.
        detail = UNREADABLE_MESSAGE
        if extraction.readability_issue:
            detail = f"{extraction.readability_issue.rstrip('. ')}. {detail}"
        raise HTTPException(status_code=422, detail=detail)

    return public_data


@app.exception_handler(HTTPException)
async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    """Translate FastAPI's HTTPException into the standard error shape."""
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})


@app.post(
    "/extract-nid",
    response_model=NIDData,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid image upload."},
        413: {"model": ErrorResponse, "description": "Upload too large."},
        422: {
            "model": ErrorResponse,
            "description": "Not an NID card, unreadable, or missing fields.",
        },
        502: {"model": ErrorResponse, "description": "AI service unavailable."},
        503: {
            "model": ErrorResponse,
            "description": "Server misconfigured (GEMINI_API_KEY not set).",
        },
    },
)
def extract_nid(front: UploadFile = File(...), back: UploadFile = File(...)) -> NIDData:
    """Extract structured Bangladesh NID data from front and back images."""
    front_bytes, back_bytes = _read_and_check_size(front, back)

    front_prepared = validate_and_prepare(
        front_bytes,
        min_dimension=config.MIN_IMAGE_DIMENSION,
        max_dimension=config.MAX_IMAGE_DIMENSION,
    )
    back_prepared = validate_and_prepare(
        back_bytes,
        min_dimension=config.MIN_IMAGE_DIMENSION,
        max_dimension=config.MAX_IMAGE_DIMENSION,
    )

    extraction = extract_nid_data(front_prepared, back_prepared)
    return _resolve_extraction_outcome(extraction)
