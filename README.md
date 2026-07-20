# Bangladesh NID Information Extractor

An AI-powered FastAPI service that reads the front and back photos of a
Bangladesh National ID (NID) card and returns the card's data as structured,
English-language JSON. A single multimodal call to Google Gemini 3.1 Flash
Lite performs OCR, semantic Bengali-to-English translation, and field
structuring in one step — no separate OCR or translation library is used.

## Features

- **Single multimodal call** — one Gemini request per submission performs
  OCR, translation, and structuring together, instead of chaining separate
  OCR/translation/parsing steps.
- **Structured output via Pydantic** — the Gemini `response_schema` is
  derived directly from a Pydantic model, so the model returns typed JSON
  rather than free text that would need regex parsing.
- **Byte-level image validation** — uploads are validated by decoding actual
  image content with Pillow (`Image.verify()` + format check), never by file
  extension or client-supplied content type.
- **EXIF-aware downscaling** — images are auto-rotated per EXIF orientation
  and downscaled so the longest side is at most 1600px, keeping Gemini
  latency and cost predictable.
- **Partial extraction is success, not failure** — any field the model
  cannot confidently read is returned as `null` in a `200` response instead
  of failing the whole request.
- **Strict JSON error contract** — every error path, regardless of cause,
  returns `{"error": "<message>"}` with an appropriate HTTP status code.
- **PII-safe logging** — only request metadata (method, path, status,
  duration, error category) is ever logged; image bytes, extracted personal
  data, and the API key are never logged.

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ (developed on 3.13) |
| Web framework | FastAPI, served by Uvicorn (ASGI) |
| AI / OCR | Google Gemini 3.1 Flash Lite via the official `google-genai` SDK |
| Data validation | Pydantic v2 (API contract + Gemini structured-output schema) |
| Image processing | Pillow (byte-level validation, EXIF rotation, downscaling) |
| Configuration | Environment variables via `python-dotenv` |
| Frontend | Single-page vanilla HTML/CSS/JS (no framework, no build step) |
| Testing | pytest + FastAPI `TestClient` (httpx), Gemini fully mocked |
| Deployment | Docker / Docker Compose |

All dependencies are pinned in [requirements.txt](requirements.txt).

## AI / OCR Approach

The only AI/OCR component is **Google Gemini 3.1 Flash Lite**
(`gemini-3.1-flash-lite`), called through the official
[`google-genai`](https://pypi.org/project/google-genai/) Python SDK. No
traditional OCR library (Tesseract, EasyOCR, etc.) and no separate
translation service is used: a single multimodal request sends both card
images plus an instruction prompt, and Gemini performs OCR, semantic
Bengali-to-English translation, and field structuring in one step. The
response is constrained to typed JSON with Gemini's structured-output mode
(`response_schema` generated from a Pydantic model), and every returned
field is then re-validated server-side (NID number digit count, ISO dates,
known blood groups) before it reaches the client.

## Setup

```bash
python -m venv .venv
```

Activate the virtual environment:

```powershell
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure your API key:

```bash
cp .env.example .env
# then edit .env and set GEMINI_API_KEY=<your-key>
```

Run the server:

```bash
uvicorn app.main:app --reload
```

- Demo UI: http://127.0.0.1:8000/
- Swagger docs: http://127.0.0.1:8000/docs

## Run with Docker

Prerequisite: Docker, and a `.env` file containing `GEMINI_API_KEY`:

```bash
cp .env.example .env
# then edit .env and set GEMINI_API_KEY=<your-key>
```

**Option A (preferred) — Docker Compose:**

```bash
docker compose up --build
```

**Option B — plain Docker:**

```bash
docker build -t nid-extractor .
docker run --rm -p 8000:8000 --env-file .env nid-extractor
```

Either way, the app is at http://localhost:8000. The image never contains
the API key or any NID images; the key is injected at runtime via
`--env-file`/`env_file`.

## API Reference

Full endpoint documentation, including field-level validation rules and
example requests, is in [API.md](API.md). A summary follows.

### `POST /extract-nid`

Accepts `multipart/form-data` with two required file fields, `front` and
`back` (JPG, JPEG, or PNG, validated by actual file content).

**Success — `200 OK`**

```json
{
  "name": "Md. Rahim",
  "fatherName": "Abdul Karim",
  "motherName": "Amena Begum",
  "dateOfBirth": "1998-01-15",
  "bloodGroup": "B+",
  "placeOfBirth": "Dhaka",
  "nidNumber": "1234567890123",
  "presentAddress": "Dhaka, Bangladesh",
  "permanentAddress": "Cumilla, Bangladesh",
  "issueDate": "2019-05-20"
}
```

The response contains ten fields in total: `name`, `fatherName`,
`motherName`, `dateOfBirth`, `bloodGroup`, `placeOfBirth`, `nidNumber`,
`presentAddress`, `permanentAddress`, and `issueDate`. `bloodGroup` and
`placeOfBirth` typically come from the back of a smart-card NID (or the back
of an older laminated card, for `bloodGroup`), and `issueDate` is normalized
to `YYYY-MM-DD` like `dateOfBirth`.

Any field the model cannot confidently read is returned as `null`; a
partially filled response is still a `200`. If the card only ever printed a
single address, `presentAddress` and `permanentAddress` are guaranteed to be
mirrored to the same value rather than one being left `null`.

**Errors** — every error response has the shape `{"error": "<message>"}`.

| Condition | Status | Message |
|---|---|---|
| Missing `front` or `back` file | 422 | Clear message naming the missing field(s) |
| Wrong format / corrupt image (Pillow verify fails) | 400 | "Image file is corrupt or not a supported format (JPG/JPEG/PNG)." |
| Image too small (< 300px on either dimension) | 400 | Message suggesting a higher-resolution photo |
| `is_nid` is false | 422 | "The uploaded images do not appear to be a Bangladesh NID card." |
| `readability_issue` set and all fields null | 422 | Surfaces the readability issue to the user |
| Gemini API failure / timeout | 502 | "AI service temporarily unavailable, please retry." (one automatic retry with backoff before failing) |
| Partial extraction (some fields null) | 200 | Data returned as-is with nulls; frontend displays "Not readable" for null fields |

## Architecture

**Request flow:**

```
upload (front, back)
  -> byte-level validation & downscale (Pillow)
  -> single Gemini structured-output call (front + back + prompt)
  -> Pydantic validation (NID number / date format)
  -> JSON response
```

**Modules:**

- `app/main.py` — FastAPI app, routes, exception handlers, request logging.
- `app/schemas.py` — Pydantic models: the public `NIDData` API contract and
  `GeminiNIDExtraction`, the internal Gemini structured-output schema.
- `app/gemini_client.py` — builds the prompt, calls Gemini with structured
  output, retries once on transient failure, parses the response.
- `app/image_utils.py` — validates uploaded bytes as real images, checks
  minimum dimensions, applies EXIF orientation, downscales, re-encodes JPEG.
- `app/config.py` — loads configuration (API key, model name, size/timeout
  limits) from environment variables via `python-dotenv`.
- `static/index.html` — a single vanilla-JS page with two file inputs, a
  submit button, a loading state, and a formatted JSON result panel.

**Design decisions:**

- **Why a single multimodal call:** Sending both images and the instructions
  in one Gemini request lets the model perform OCR, translation, and
  structuring together, avoiding the latency, cost, and error-accumulation
  of chaining separate OCR/translation/parsing services.
- **Why `response_schema` from Pydantic instead of regex parsing:** Gemini's
  structured-output mode guarantees a JSON shape that matches the schema, so
  the same Pydantic model documents the contract and validates it — brittle
  regex extraction over free-form text is unnecessary.
- **Why nulls instead of errors for partial reads:** A card photo often has
  one illegible field (e.g. a smudged NID number); failing the whole request
  would force needless re-uploads, so partial data is returned as a `200`
  with `null` for anything not confidently read, and the caller decides how
  to handle gaps.
- **Why PII-safe logging:** In a fintech context, NID numbers, names, and
  addresses are sensitive personal data; logging only metadata (status,
  duration, error category) keeps the audit trail useful for debugging
  without ever persisting extracted PII or the API key.

## Testing

```bash
pytest
```

All tests mock the Gemini call (`app.main.extract_nid_data` is monkeypatched)
and generate test images in memory with Pillow, so the suite runs fully
offline — no `GEMINI_API_KEY` is required and no network call is ever made.

## Project Structure

```
IDLCFinanceTakeHome/
├── app/
│   ├── main.py          # FastAPI app, routes, static file serving
│   ├── schemas.py        # Pydantic models (API response + Gemini schema)
│   ├── gemini_client.py  # Gemini call, prompt, retry logic
│   ├── image_utils.py    # validation, verify, downscale
│   └── config.py         # env/config loading
├── static/
│   └── index.html        # upload UI
├── tests/
│   └── test_api.py       # mocked endpoint tests: missing fields, bad
│                          # format, too small, happy path, non-NID,
│                          # readability, Gemini failure, schema validation
├── conftest.py            # puts the repo root on sys.path for pytest
├── requirements.txt
├── .env.example
├── .dockerignore
├── docker-compose.yml
├── Dockerfile
├── README.md
└── PROJECT.md             # requirements source of truth
```
