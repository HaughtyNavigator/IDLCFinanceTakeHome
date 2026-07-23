"""Client wrapper around the Gemini API for Bangladesh NID data extraction.

This module never logs image bytes, extracted field values, or the API key.
Only exception class names / high-level error categories may be logged by
callers.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from google.genai import Client, errors, types
from pydantic import ValidationError

from app import config
from app.consensus import build_consensus, minimum_agreement
from app.schemas import GeminiNIDExtraction

logger = logging.getLogger("nid_extractor")

_PROMPT = """\
You are analyzing two photographs of a Bangladesh National ID (NID) card.
Image 1 is the FRONT of the card and image 2 is the BACK.

Both the older laminated paper NID layout and the newer smart-card NID
layout are in use in Bangladesh. Handle either layout correctly.

Extract the following fields:
- From the front: name, father's name, mother's name, date of birth, and
  the NID number.
- From the back: present address and permanent address. Note that on some
  card layouts the address may appear only once, or may appear on the
  front instead of the back -- extract it from wherever it is found. If
  the card shows only a single address (many layouts do), return that same
  address in BOTH presentAddress and permanentAddress; only return
  different values for the two fields when the card explicitly shows two
  distinct addresses.
- Blood group, place of birth, and issue date. Their position varies by
  layout: on smart cards, blood group and place of birth are typically
  printed on the BACK, and the issue date appears near the bottom of the
  card. On older laminated cards, blood group (if present) and the issue
  date ("প্রদানের তারিখ") usually appear on the back as well. Extract each
  of these three fields from wherever it is actually found on either image.

Translation rules:
- Translate all Bengali text to English SEMANTICALLY, not word-for-word.
  Use standard Bangladeshi romanization conventions for names and honorific
  particles, for example "মোঃ" -> "Md." and "বেগম" -> "Begum".
- If text is already in English, keep it as-is.

Formatting rules:
- Normalize the date of birth to the YYYY-MM-DD format.
- Normalize the issue date to the YYYY-MM-DD format.
- Extract the blood group exactly as printed, e.g. "B+".
- Translate the place of birth to English, like other translated fields.
- If any field cannot be read with confidence, return null for that field.
  NEVER guess or fabricate a value.

Transcription rules (important):
- TRANSCRIBE only characters that are physically visible in the image. Do
  NOT reconstruct, autocomplete, or infer a value from context, from what is
  typical on Bangladeshi NID cards, or from other fields on the card.
- A plausible-looking value is NOT the same as a legible one. If a name,
  digit, or word is smudged, cut off, glared over, or out of focus, return
  null for that field instead of filling in what it "probably" says.

Validity rules:
- Set is_nid to false if the images do not actually appear to depict a
  Bangladesh NID card (front and back).
- If the images are blurry, poorly lit, cropped, or otherwise unreadable,
  set readability_issue to a short description of the problem. Otherwise
  set readability_issue to null.
"""

_MAX_ATTEMPTS = 2
_TRANSIENT_ERRORS = (errors.APIError, ConnectionError, TimeoutError)


class GeminiServiceError(Exception):
    """Raised when the AI service fails or returns an unusable response."""


class GeminiConfigurationError(GeminiServiceError):
    """Raised when the server is missing the Gemini API key.

    A subclass of GeminiServiceError so existing callers that handle the
    parent class keep working, while the API layer can distinguish a server
    misconfiguration (operator must set a variable) from a genuine upstream
    outage (client should retry).
    """


def _build_config() -> types.GenerateContentConfig:
    """Build the Gemini generation config for structured NID extraction."""
    return types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=GeminiNIDExtraction,
        http_options=types.HttpOptions(timeout=config.GEMINI_TIMEOUT_SECONDS * 1000),
    )


def _call_gemini(
    client: Client, front_jpeg: bytes, back_jpeg: bytes
) -> types.GenerateContentResponse:
    """Call the Gemini model once, retrying a single time on transient errors.

    Raises:
        GeminiServiceError: If the call still fails after one retry.
    """
    contents = [
        _PROMPT,
        types.Part.from_bytes(data=front_jpeg, mime_type="image/jpeg"),
        types.Part.from_bytes(data=back_jpeg, mime_type="image/jpeg"),
    ]
    generation_config = _build_config()

    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=contents,
                config=generation_config,
            )
        except _TRANSIENT_ERRORS as exc:
            last_exc = exc
            if attempt + 1 < _MAX_ATTEMPTS:
                time.sleep(config.GEMINI_RETRY_DELAY_SECONDS)

    raise GeminiServiceError("AI service call failed after retry.") from last_exc


def _parse_response(
    response: types.GenerateContentResponse,
) -> GeminiNIDExtraction:
    """Parse a Gemini response into a GeminiNIDExtraction.

    Prefers the SDK's own parsed structured output and falls back to
    manually validating the raw JSON text.

    Raises:
        GeminiServiceError: If the response cannot be parsed into a usable
            extraction result.
    """
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, GeminiNIDExtraction):
        return parsed

    try:
        return GeminiNIDExtraction.model_validate_json(response.text)
    except (ValidationError, ValueError, TypeError) as exc:
        raise GeminiServiceError("AI service returned an unusable response.") from exc


def _extract_once(
    client: Client, front_jpeg: bytes, back_jpeg: bytes
) -> GeminiNIDExtraction:
    """Run a single extraction: one API call plus response parsing."""
    return _parse_response(_call_gemini(client, front_jpeg, back_jpeg))


def _gather_samples(
    client: Client, front_jpeg: bytes, back_jpeg: bytes, sample_count: int
) -> list[GeminiNIDExtraction]:
    """Run several independent extractions concurrently.

    Samples are issued in parallel so total latency stays close to that of a
    single call. Samples that fail are dropped rather than failing the whole
    request; the consensus is then taken over whichever succeeded.

    Raises:
        GeminiServiceError: If every sample failed.
    """
    with ThreadPoolExecutor(max_workers=sample_count) as executor:
        futures = [
            executor.submit(_extract_once, client, front_jpeg, back_jpeg)
            for _ in range(sample_count)
        ]
        extractions: list[GeminiNIDExtraction] = []
        for future in futures:
            try:
                extractions.append(future.result())
            except (GeminiServiceError, ValidationError) as exc:
                logger.warning("Extraction sample failed: %s", type(exc).__name__)

    if not extractions:
        raise GeminiServiceError("All extraction attempts failed.")
    return extractions


def extract_nid_data(front_jpeg: bytes, back_jpeg: bytes) -> GeminiNIDExtraction:
    """Extract structured NID data from prepared front and back JPEG images.

    Runs ``config.EXTRACTION_SAMPLES`` independent extractions and keeps only
    the field values that agree across a majority of them, so that values the
    model reconstructed from an unclear image — which vary between runs —
    are discarded instead of returned. Note that the sampling temperature is
    deliberately left at the model default: at temperature zero every sample
    would be identical and the agreement check would detect nothing.

    Args:
        front_jpeg: Normalized JPEG bytes for the front of the NID card.
        back_jpeg: Normalized JPEG bytes for the back of the NID card.

    Returns:
        The agreed, validated NID data.

    Raises:
        GeminiConfigurationError: If the API key is not configured.
        GeminiServiceError: If every extraction attempt fails, or a response
            cannot be parsed.
    """
    if not config.GEMINI_API_KEY:
        raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")

    client = Client(api_key=config.GEMINI_API_KEY)
    sample_count = config.extraction_samples()
    if sample_count == 1:
        logger.info("EXTRACTION_SAMPLES=1: single extraction, no agreement check")
        return _extract_once(client, front_jpeg, back_jpeg)

    logger.info("running %d extraction samples", sample_count)
    extractions = _gather_samples(client, front_jpeg, back_jpeg, sample_count)
    if len(extractions) < sample_count:
        logger.warning(
            "%d of %d samples failed; voting over the survivors",
            sample_count - len(extractions),
            sample_count,
        )
    return build_consensus(extractions, minimum_agreement(len(extractions)))
