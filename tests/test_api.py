"""Tests for the ``POST /extract-nid`` endpoint.

All tests monkeypatch ``app.main.extract_nid_data`` so the real Gemini API is
never called: validation (missing fields, corrupt/small images) happens
before that function is invoked, and every test that reaches it replaces it
with a stub. No test in this module makes a network call or requires
``GEMINI_API_KEY`` to be set.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.gemini_client import GeminiServiceError
from app.main import app
from app.schemas import GeminiNIDExtraction

MultipartFiles = dict[str, tuple[str, bytes, str]]


@pytest.fixture
def client() -> TestClient:
    """Return a ``TestClient`` that surfaces app-level exception handlers.

    ``raise_server_exceptions=False`` ensures any unhandled exception is
    turned into the app's registered 500 response instead of propagating
    into the test process, matching real server behavior.
    """
    return TestClient(app, raise_server_exceptions=False)


def make_jpeg(width: int, height: int) -> bytes:
    """Build an in-memory JPEG of the given dimensions using Pillow."""
    buffer = BytesIO()
    image = Image.new("RGB", (width, height), color=(120, 140, 160))
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def make_gif(width: int, height: int) -> bytes:
    """Build an in-memory GIF (an unsupported format) using Pillow."""
    buffer = BytesIO()
    image = Image.new("RGB", (width, height), color=(10, 20, 30))
    image.save(buffer, format="GIF")
    return buffer.getvalue()


def build_files(
    front_bytes: bytes | None,
    back_bytes: bytes | None,
    front_name: str = "front.jpg",
    back_name: str = "back.jpg",
    content_type: str = "image/jpeg",
) -> MultipartFiles:
    """Build a multipart ``files`` dict for the ``/extract-nid`` endpoint.

    Either side may be omitted (``None``) to simulate a missing file field.
    """
    files: MultipartFiles = {}
    if front_bytes is not None:
        files["front"] = (front_name, front_bytes, content_type)
    if back_bytes is not None:
        files["back"] = (back_name, back_bytes, content_type)
    return files


def patch_extract(
    monkeypatch: pytest.MonkeyPatch,
    *,
    return_value: GeminiNIDExtraction | None = None,
    side_effect: Exception | None = None,
) -> None:
    """Monkeypatch ``app.main.extract_nid_data`` for a single test.

    Exactly one of ``return_value`` or ``side_effect`` should be given. This
    guarantees the real Gemini client is never constructed or called.
    """

    def _stub(front_jpeg: bytes, back_jpeg: bytes) -> GeminiNIDExtraction:
        if side_effect is not None:
            raise side_effect
        assert return_value is not None
        return return_value

    monkeypatch.setattr("app.main.extract_nid_data", _stub)


FULL_EXTRACTION_KWARGS: dict[str, Any] = {
    "is_nid": True,
    "name": "Md. Rahim",
    "fatherName": "Abdul Karim",
    "motherName": "Amena Begum",
    "dateOfBirth": "1998-01-15",
    "bloodGroup": "B+",
    "placeOfBirth": "Dhaka",
    "nidNumber": "1234567890123",
    "presentAddress": "Dhaka, Bangladesh",
    "permanentAddress": "Cumilla, Bangladesh",
    "issueDate": "2019-05-20",
}

EXPECTED_FULL_RESPONSE: dict[str, str] = {
    "name": "Md. Rahim",
    "fatherName": "Abdul Karim",
    "motherName": "Amena Begum",
    "dateOfBirth": "1998-01-15",
    "bloodGroup": "B+",
    "placeOfBirth": "Dhaka",
    "nidNumber": "1234567890123",
    "presentAddress": "Dhaka, Bangladesh",
    "permanentAddress": "Cumilla, Bangladesh",
    "issueDate": "2019-05-20",
}

ALL_PUBLIC_KEYS = frozenset(EXPECTED_FULL_RESPONSE.keys())


def test_missing_both_files_returns_422(client: TestClient) -> None:
    """Missing both ``front`` and ``back`` yields 422 naming both fields."""
    response = client.post("/extract-nid", files={})

    assert response.status_code == 422
    body = response.json()
    assert "front" in body["error"]
    assert "back" in body["error"]


def test_missing_back_file_returns_422(client: TestClient) -> None:
    """Missing only ``back`` yields 422 with an error naming ``back``."""
    files = build_files(make_jpeg(400, 400), None)

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 422
    body = response.json()
    assert "back" in body["error"]


def test_corrupt_front_bytes_returns_400(client: TestClient) -> None:
    """Non-image bytes for ``front`` yield 400 with the exact corrupt-format message."""
    files = build_files(b"not an image", make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 400
    assert response.json() == {
        "error": "Image file is corrupt or not a supported format (JPG/JPEG/PNG)."
    }


def test_unsupported_gif_format_returns_400(client: TestClient) -> None:
    """A well-formed but unsupported image format (GIF) yields 400 with the same message."""
    files = build_files(make_gif(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 400
    assert response.json() == {
        "error": "Image file is corrupt or not a supported format (JPG/JPEG/PNG)."
    }


def test_too_small_image_returns_400(client: TestClient) -> None:
    """An image below the minimum dimension (e.g. 200x200) yields 400 mentioning size."""
    files = build_files(make_jpeg(200, 200), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 400
    error_message = response.json()["error"].lower()
    assert "small" in error_message or "resolution" in error_message


def test_happy_path_returns_full_public_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full mocked extraction returns 200 with exactly the 10-key public JSON."""
    patch_extract(
        monkeypatch,
        return_value=GeminiNIDExtraction(**FULL_EXTRACTION_KWARGS),
    )
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 200
    assert response.json() == EXPECTED_FULL_RESPONSE


def test_partial_extraction_preserves_null_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Partial extraction (only ``name`` set) returns 200 with all ten keys present, others null."""
    patch_extract(
        monkeypatch,
        return_value=GeminiNIDExtraction(is_nid=True, name="Md. Rahim"),
    )
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == ALL_PUBLIC_KEYS
    assert body["name"] == "Md. Rahim"
    for key in ALL_PUBLIC_KEYS - {"name"}:
        assert body[key] is None


def test_non_nid_images_return_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``is_nid=False`` yields 422 with the exact non-NID rejection message."""
    patch_extract(monkeypatch, return_value=GeminiNIDExtraction(is_nid=False))
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 422
    assert response.json() == {
        "error": "The uploaded images do not appear to be a Bangladesh NID card."
    }


def test_readability_issue_with_no_fields_returns_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A readability issue with all fields null surfaces the issue text in a 422."""
    patch_extract(
        monkeypatch,
        return_value=GeminiNIDExtraction(
            is_nid=True,
            readability_issue="Images are too blurry to read",
        ),
    )
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 422
    assert response.json()["error"] == "Images are too blurry to read"


def test_gemini_service_error_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``GeminiServiceError`` from the extraction call yields a 502 with the generic message."""
    patch_extract(
        monkeypatch,
        side_effect=GeminiServiceError("AI service call failed after retry."),
    )
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 502
    assert response.json() == {
        "error": "AI service temporarily unavailable, please retry."
    }


def test_invalid_nid_number_is_nulled_by_schema_validation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invalid ``nidNumber`` (too few digits) is nulled by Pydantic validation."""
    patch_extract(
        monkeypatch,
        return_value=GeminiNIDExtraction(nidNumber="12345", is_nid=True),
    )
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 200
    assert response.json()["nidNumber"] is None


def test_invalid_blood_group_is_nulled_by_schema_validation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invalid ``bloodGroup`` (not a recognized group) is nulled by Pydantic validation."""
    patch_extract(
        monkeypatch,
        return_value=GeminiNIDExtraction(bloodGroup="XY", is_nid=True),
    )
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 200
    assert response.json()["bloodGroup"] is None


def test_present_address_only_mirrors_to_permanent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only ``presentAddress`` set mirrors into ``permanentAddress`` too."""
    patch_extract(
        monkeypatch,
        return_value=GeminiNIDExtraction(
            is_nid=True, presentAddress="Dhaka, Bangladesh"
        ),
    )
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 200
    body = response.json()
    assert body["presentAddress"] == "Dhaka, Bangladesh"
    assert body["permanentAddress"] == "Dhaka, Bangladesh"


def test_permanent_address_only_mirrors_to_present(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only ``permanentAddress`` set mirrors into ``presentAddress`` too."""
    patch_extract(
        monkeypatch,
        return_value=GeminiNIDExtraction(
            is_nid=True, permanentAddress="Cumilla, Bangladesh"
        ),
    )
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 200
    body = response.json()
    assert body["presentAddress"] == "Cumilla, Bangladesh"
    assert body["permanentAddress"] == "Cumilla, Bangladesh"


def test_distinct_addresses_are_preserved_unchanged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two distinct addresses are both preserved without mirroring."""
    patch_extract(
        monkeypatch,
        return_value=GeminiNIDExtraction(
            is_nid=True,
            presentAddress="Dhaka, Bangladesh",
            permanentAddress="Cumilla, Bangladesh",
        ),
    )
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 200
    body = response.json()
    assert body["presentAddress"] == "Dhaka, Bangladesh"
    assert body["permanentAddress"] == "Cumilla, Bangladesh"


def test_messy_blood_group_is_normalized(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A messy but valid ``bloodGroup`` (e.g. ``"b +"``) normalizes to ``"B+"``."""
    patch_extract(
        monkeypatch,
        return_value=GeminiNIDExtraction(bloodGroup="b +", is_nid=True),
    )
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 200
    assert response.json()["bloodGroup"] == "B+"
