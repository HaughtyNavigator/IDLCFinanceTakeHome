"""Tests for the ``POST /extract-nid`` endpoint.

All tests monkeypatch ``app.main.extract_nid_data`` so the real Gemini API is
never called: validation (missing fields, corrupt/small images) happens
before that function is invoked, and every test that reaches it replaces it
with a stub. No test in this module makes a network call or requires
``GEMINI_API_KEY`` to be set.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.consensus import MatchPolicy, build_consensus, minimum_agreement, vote
from app.gemini_client import GeminiConfigurationError, GeminiServiceError
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


def readable_extraction(**overrides: Any) -> GeminiNIDExtraction:
    """Build an extraction that passes the unreadable-field limit.

    Tests that exercise something other than that limit start from a fully
    populated card and override only the field under test, so they are not
    rejected for being too sparse.
    """
    return GeminiNIDExtraction(**{**FULL_EXTRACTION_KWARGS, **overrides})


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
    """A partial extraction within the limit returns 200 with all ten keys present."""
    missing = {"placeOfBirth": None, "issueDate": None, "motherName": None}
    patch_extract(monkeypatch, return_value=readable_extraction(**missing))
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == ALL_PUBLIC_KEYS
    assert body["name"] == "Md. Rahim"
    for key in missing:
        assert body[key] is None


def test_no_agreed_fields_returns_unreadable_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no field survives the agreement check, the images are rejected as unclear."""
    patch_extract(monkeypatch, return_value=GeminiNIDExtraction(is_nid=True))
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 422
    assert "not clear enough" in response.json()["error"]


def test_eight_unreadable_fields_are_rejected_instead_of_returned(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eight missing fields is one too many: no partial JSON, a retry ask instead."""
    patch_extract(
        monkeypatch,
        return_value=readable_extraction(
            motherName=None,
            dateOfBirth=None,
            placeOfBirth=None,
            issueDate=None,
            bloodGroup=None,
            nidNumber=None,
            presentAddress=None,
            permanentAddress=None,
        ),
    )
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 422
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert "not clear enough" in body["error"]
    assert "try again" in body["error"]


def test_seven_unreadable_fields_are_still_returned(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seven missing fields is the documented limit and still yields a 200."""
    patch_extract(
        monkeypatch,
        return_value=readable_extraction(
            motherName=None,
            dateOfBirth=None,
            placeOfBirth=None,
            issueDate=None,
            bloodGroup=None,
            presentAddress=None,
            permanentAddress=None,
        ),
    )
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Md. Rahim"
    assert sum(1 for value in body.values() if value is None) == 7


def test_vote_keeps_value_agreed_by_majority() -> None:
    """A value produced by two of three samples is accepted."""
    assert vote(["Md. Rahim", "Md. Rahim", "Md. Rahmn"], 2) == "Md. Rahim"


def test_vote_discards_value_when_every_sample_differs() -> None:
    """Three different readings of the same field agree on nothing."""
    assert vote(["1234567890", "1234567891", "1234567892"], 2) is None


def test_vote_ignores_cosmetic_differences() -> None:
    """Whitespace, case and trailing punctuation do not count as disagreement."""
    assert vote(["Md. Rahim", "md.  rahim ", "Md. Rahim."], 2) == "Md. Rahim"


def test_vote_requires_agreement_against_all_samples() -> None:
    """A field only one of three samples read at all is not accepted."""
    assert vote(["Dhaka", None, None], 2) is None


def test_vote_returns_none_when_no_sample_read_the_field() -> None:
    """A field every sample left empty stays empty."""
    assert vote([None, None, None], 2) is None


# Three readings of one synthetic address, shaped after a real observed
# failure: two differ by a single period, the third translates the printed
# form labels differently. No real card data is used in tests.
ADDRESS_READINGS = [
    "House/Holding: 10/5, Village/Road: 3, Block-B, Block No-B, "
    "Post Office: Mirpur - 1216, Mirpur, Dhaka North City Corporation, Dhaka",
    "House/Holding: 10/5, Village/Road: 3, Block-B, Block No.-B, "
    "Post Office: Mirpur - 1216, Mirpur, Dhaka North City Corporation, Dhaka",
    "Holding No: 10/5, Road: 3, Block-B, "
    "Post Office: Mirpur - 1216, Mirpur, Dhaka North City Corporation, Dhaka",
]


def test_address_readings_agree_despite_label_wording() -> None:
    """Same address written three ways counts as agreement under fuzzy matching."""
    assert vote(ADDRESS_READINGS, 2, policy=MatchPolicy.FUZZY) in ADDRESS_READINGS
    # The same readings are a three-way disagreement under exact matching,
    # which is what made the addresses disappear in the first place.
    assert vote(ADDRESS_READINGS, 2) is None


def test_genuinely_different_addresses_still_disagree() -> None:
    """Fuzzy matching accepts rewording, not three unrelated addresses."""
    readings = [
        "10/5, Road 3, Block B, Mirpur, Dhaka - 1216",
        "42, Kazi Nazrul Islam Avenue, Kawran Bazar, Dhaka - 1215",
        "7/A, College Road, Chawkbazar, Chattogram - 4000",
    ]

    assert vote(readings, 2, policy=MatchPolicy.FUZZY) is None


def test_build_consensus_applies_fuzzy_matching_only_to_addresses() -> None:
    """Addresses tolerate rewording; a differing NID digit is still a disagreement."""
    samples = [
        GeminiNIDExtraction(is_nid=True, presentAddress=reading, nidNumber=nid_number)
        for reading, nid_number in zip(
            ADDRESS_READINGS, ["1234567890123", "1234567898123", "1234561890123"]
        )
    ]

    merged = build_consensus(samples, minimum_agreement(len(samples)))

    assert merged.presentAddress in ADDRESS_READINGS
    assert merged.permanentAddress == merged.presentAddress
    # 0.92 similar to each other, and still rejected: a wrong digit is a
    # wrong number, not a rewording.
    assert merged.nidNumber is None


def test_strict_policy_rejects_a_majority_built_on_a_disputed_digit() -> None:
    """A majority is not enough when a dissenting sample differs by one digit.

    Taken from a real degraded-image run: three samples agreed on a date
    that was wrong, and the two dissenters differed from it by a single
    digit. A simple majority returned the wrong date with confidence.
    """
    readings = ["1994-07-17", "1994-07-17", "1994-07-17", "1994-07-07", "1997-07-17"]

    assert vote(readings, 3) == "1994-07-17"  # what a simple majority does
    assert vote(readings, 3, policy=MatchPolicy.STRICT) is None


def test_strict_policy_keeps_a_unanimous_reading() -> None:
    """Agreement with no dissent at all is still accepted."""
    readings = ["3314871546"] * 5

    assert vote(readings, 3, policy=MatchPolicy.STRICT) == "3314871546"


def test_strict_policy_ignores_an_unrelated_dissent() -> None:
    """A dissent that resembles nothing is a misread sample, not a disputed digit."""
    readings = ["3314871546", "3314871546", "2024-10-29"]

    assert vote(readings, 2, policy=MatchPolicy.STRICT) == "3314871546"


def test_strict_policy_tolerates_samples_that_read_nothing() -> None:
    """A sample leaving the field empty is not a dissenting reading."""
    readings = ["3314871546", "3314871546", None]

    assert vote(readings, 2, policy=MatchPolicy.STRICT) == "3314871546"


def test_build_consensus_applies_the_strict_policy_to_digit_fields() -> None:
    """Digit and date fields veto on a near-miss; names keep simple majority."""
    samples = [
        GeminiNIDExtraction(
            is_nid=True,
            nidNumber=nid_number,
            dateOfBirth=date_of_birth,
            name=name,
        )
        for nid_number, date_of_birth, name in [
            ("1234567890123", "1994-07-17", "Md. Rahim"),
            ("1234567890123", "1994-07-17", "Md. Rahim"),
            ("1234567898123", "1994-07-07", "Md. Rahmn"),
        ]
    ]

    merged = build_consensus(samples, minimum_agreement(len(samples)))

    assert merged.nidNumber is None
    assert merged.dateOfBirth is None
    # Names were deliberately left on simple majority: romanization of the
    # same Bengali text varies legitimately between runs.
    assert merged.name == "Md. Rahim"


def test_minimum_agreement_is_a_simple_majority() -> None:
    """One of one, two of two, two of three, three of five."""
    assert [minimum_agreement(n) for n in (1, 2, 3, 4, 5)] == [1, 2, 2, 3, 3]


def test_build_consensus_keeps_agreed_fields_and_drops_the_rest() -> None:
    """Agreed fields survive; fields that differ between samples are nulled."""
    samples = [
        GeminiNIDExtraction(is_nid=True, name="Md. Rahim", nidNumber="1234567890"),
        GeminiNIDExtraction(is_nid=True, name="Md. Rahim", nidNumber="1234567891"),
        GeminiNIDExtraction(is_nid=True, name="Md. Rahim", nidNumber="1234567892"),
    ]

    merged = build_consensus(samples, minimum_agreement(len(samples)))

    assert merged.name == "Md. Rahim"
    assert merged.nidNumber is None
    assert merged.is_nid is True


def test_build_consensus_rejects_non_nid_by_majority() -> None:
    """If most samples say the images are not an NID card, the consensus agrees."""
    samples = [
        GeminiNIDExtraction(is_nid=False),
        GeminiNIDExtraction(is_nid=False),
        GeminiNIDExtraction(is_nid=True),
    ]

    assert build_consensus(samples, minimum_agreement(len(samples))).is_nid is False


def test_consensus_logging_hides_values_by_default(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The vote is logged per field, but extracted PII is not."""
    monkeypatch.setattr("app.config.CONSENSUS_LOG_VALUES", "false")
    samples = [
        GeminiNIDExtraction(is_nid=True, name="Md. Rahim", nidNumber="1234567890"),
        GeminiNIDExtraction(is_nid=True, name="Md. Rahim", nidNumber="1234567891"),
    ]

    with caplog.at_level(logging.INFO, logger="nid_extractor.consensus"):
        build_consensus(samples, minimum_agreement(len(samples)))

    logged = caplog.text
    assert "name" in logged and "nidNumber" in logged
    assert "2/2" in logged
    assert "Md. Rahim" not in logged
    assert "1234567890" not in logged


def test_consensus_logging_shows_values_when_explicitly_enabled(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The debug flag opts in to logging the agreed values and the candidates."""
    monkeypatch.setattr("app.config.CONSENSUS_LOG_VALUES", "true")
    samples = [
        GeminiNIDExtraction(is_nid=True, name="Md. Rahim", nidNumber="1234567890"),
        GeminiNIDExtraction(is_nid=True, name="Md. Rahim", nidNumber="1234567891"),
    ]

    with caplog.at_level(logging.INFO, logger="nid_extractor.consensus"):
        build_consensus(samples, minimum_agreement(len(samples)))

    logged = caplog.text
    assert "Md. Rahim" in logged
    assert "1234567890" in logged and "1234567891" in logged


def test_consensus_log_values_defaults_to_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only an explicit truthy value enables PII logging; a typo fails safe."""
    monkeypatch.setattr("app.config.CONSENSUS_LOG_VALUES", "ture")
    assert config.consensus_log_values() is False
    monkeypatch.setattr("app.config.CONSENSUS_LOG_VALUES", "TRUE")
    assert config.consensus_log_values() is True


def test_extraction_samples_clamps_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad or oversized sample count cannot issue unbounded API calls."""
    monkeypatch.setattr("app.config.EXTRACTION_SAMPLES", "99")
    assert config.extraction_samples() == config.MAX_EXTRACTION_SAMPLES
    monkeypatch.setattr("app.config.EXTRACTION_SAMPLES", "not-a-number")
    assert config.extraction_samples() == 3


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
    """A readability issue on a rejected extraction is surfaced with the retry ask."""
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
    error = response.json()["error"]
    assert error.startswith("Images are too blurry to read.")
    assert "try again" in error


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


def test_missing_api_key_returns_503_naming_the_variable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing ``GEMINI_API_KEY`` yields a 503 that names the variable, not a 502."""
    patch_extract(
        monkeypatch,
        side_effect=GeminiConfigurationError("GEMINI_API_KEY is not configured."),
    )
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 503
    error = response.json()["error"]
    assert "GEMINI_API_KEY is not set" in error


def test_invalid_nid_number_is_nulled_by_schema_validation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invalid ``nidNumber`` (too few digits) is nulled by Pydantic validation."""
    patch_extract(monkeypatch, return_value=readable_extraction(nidNumber="12345"))
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 200
    assert response.json()["nidNumber"] is None


def test_invalid_blood_group_is_nulled_by_schema_validation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invalid ``bloodGroup`` (not a recognized group) is nulled by Pydantic validation."""
    patch_extract(monkeypatch, return_value=readable_extraction(bloodGroup="XY"))
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
        return_value=readable_extraction(
            presentAddress="Dhaka, Bangladesh", permanentAddress=None
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
        return_value=readable_extraction(
            presentAddress=None, permanentAddress="Cumilla, Bangladesh"
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
        return_value=readable_extraction(
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
    patch_extract(monkeypatch, return_value=readable_extraction(bloodGroup="b +"))
    files = build_files(make_jpeg(400, 400), make_jpeg(400, 400))

    response = client.post("/extract-nid", files=files)

    assert response.status_code == 200
    assert response.json()["bloodGroup"] == "B+"
