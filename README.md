# Bangladesh NID Information Extractor

An AI-powered FastAPI service that reads the front and back photos of a
Bangladesh National ID (NID) card and returns the card's data as structured,
English-language JSON. A single multimodal call to Google Gemini 3.1 Flash
Lite performs OCR, semantic Bengali-to-English translation, and field
structuring in one step, with no separate OCR or translation library.

> **Note for evaluators:** [AI_Usage.md](AI_Usage.md) documents how AI tools
> were used to build this project.

## Features

- **Single multimodal call**: one Gemini request per submission performs
  OCR, translation, and structuring together, instead of chaining separate
  OCR/translation/parsing steps.
- **Structured output via Pydantic**: the Gemini `response_schema` is
  derived directly from a Pydantic model, so the model returns typed JSON
  rather than free text that would need regex parsing.
- **Byte-level image validation**: uploads are validated by decoding actual
  image content with Pillow (`Image.verify()` + format check), never by file
  extension or client-supplied content type.
- **EXIF-aware downscaling**: images are auto-rotated per EXIF orientation
  and downscaled so the longest side is at most 1600px, keeping Gemini
  latency and cost predictable.
- **Partial extraction is success, up to a point**: any field the model
  cannot confidently read is returned as `null` in a `200` response instead
  of failing the whole request, and the UI names the missing fields and asks
  for a better photo. Past seven missing fields the result is too sparse to
  be useful, so the request is rejected with a `422` asking the user to
  retry rather than returning a near-empty object.
- **Self-consistency voting**: each request runs several independent
  extractions and returns only the field values that agree across a majority
  of them. Genuine readings are stable between runs while hallucinations
  vary, so disagreement is treated as an unreadable field and returned as
  `null`. Matching is per-field: addresses merge near-identical readings,
  names need an exact majority, and numbers and dates additionally reject
  any value a dissenting sample read slightly differently.
- **Strict JSON error contract**: every error path, regardless of cause,
  returns `{"error": "<message>"}` with an appropriate HTTP status code.
- **Observable voting**: the terminal shows one line per field per request
  (`KEPT 3/3` / `DROPPED 1/3` / `EMPTY 0/3`), so it is visible exactly which
  fields the samples agreed on and which were discarded.
- **PII-safe logging**: only request metadata (method, path, status,
  duration, error category, per-field vote counts) is ever logged; image
  bytes, extracted personal data, and the API key are never logged. The
  `CONSENSUS_LOG_VALUES` flag can add the extracted values to the vote log
  for local debugging, and is off by default for that reason.

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

Prerequisite: Docker, plus a Google Gemini API key.

**Option A (preferred): Docker Compose.** Either supply the key inline:

```bash
GEMINI_API_KEY=<your-key> docker compose up --build
```

or put it in a `.env` file first and just run `docker compose up --build`:

```bash
cp .env.example .env
# then edit .env and set GEMINI_API_KEY=<your-key>
docker compose up --build
```

**Option B: plain Docker.**

```bash
docker build -t nid-extractor .
docker run --rm -p 8000:8000 -e GEMINI_API_KEY=<your-key> nid-extractor
```

Either way, the app is at http://localhost:8000. The image never contains
the API key or any NID images; the key is injected at runtime only.

`.env` is optional; Compose starts without it. If no key is found the
container still comes up (so it does not crash-loop), logs a critical
startup error, and returns a `503` naming the missing variable on any
extraction request.

## API Reference

Full endpoint documentation, including field-level validation rules and
example requests, is in [API.md](API.md). A summary follows.

### `POST /extract-nid`

Accepts `multipart/form-data` with two required file fields, `front` and
`back` (JPG, JPEG, or PNG, validated by actual file content).

**Success: `200 OK`**

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
partially filled response is still a `200`, as long as no more than seven of
the ten fields are missing. At eight or more the service returns a `422`
asking for a clearer photo instead. If the card only ever printed a
single address, `presentAddress` and `permanentAddress` are guaranteed to be
mirrored to the same value rather than one being left `null`.

**Errors:** every error response has the shape `{"error": "<message>"}`.

| Condition | Status | Message |
|---|---|---|
| Missing `front` or `back` file | 422 | Clear message naming the missing field(s) |
| Wrong format / corrupt image (Pillow verify fails) | 400 | "Image file is corrupt or not a supported format (JPG/JPEG/PNG)." |
| Image too small (< 300px on either dimension) | 400 | Message suggesting a higher-resolution photo |
| `is_nid` is false | 422 | "The uploaded images do not appear to be a Bangladesh NID card." |
| 8+ of the 10 fields not agreed across samples | 422 | Message asking for sharper photos and a retry (prefixed with the model's own readability complaint, if any) |
| Gemini API failure / timeout | 502 | "AI service temporarily unavailable, please retry." (one automatic retry with backoff before failing) |
| `GEMINI_API_KEY` not set on the server | 503 | Message naming the missing variable (operator error, not client error) |
| Partial extraction (1-7 fields null) | 200 | Data returned as-is with nulls; the frontend marks each as "Not readable" and shows a banner naming them and asking for a better photo |

## Architecture

**Request flow:**

```
upload (front, back)
  -> byte-level validation & downscale (Pillow)
  -> N concurrent Gemini structured-output calls (front + back + prompt)
  -> field-level agreement vote across the samples
  -> Pydantic validation (NID number / date format)
  -> JSON response
```

**Modules:**

- `app/main.py`: FastAPI app, routes, exception handlers, request logging.
- `app/schemas.py`: Pydantic models, namely the public `NIDData` API
  contract and `GeminiNIDExtraction`, the internal Gemini structured-output
  schema.
- `app/gemini_client.py`: builds the prompt, runs the concurrent Gemini
  samples with structured output, retries once on transient failure, parses
  the responses.
- `app/consensus.py`: pure voting logic, comparing the samples field by
  field under a per-field match policy (similarity, exact, or strict) and
  keeping only the values that survive it.
- `app/image_utils.py`: validates uploaded bytes as real images, checks
  minimum dimensions, applies EXIF orientation, downscales, re-encodes JPEG.
- `app/config.py`: loads configuration (API key, model name, size/timeout
  limits) from environment variables via `python-dotenv`.
- `static/index.html`: a single vanilla-JS page with two file inputs, a
  submit button, a loading state, and a formatted JSON result panel.

**Design decisions:**

- **Why a single multimodal call:** Sending both images and the instructions
  in one Gemini request lets the model perform OCR, translation, and
  structuring together, avoiding the latency, cost, and error-accumulation
  of chaining separate OCR/translation/parsing services.
- **Why `response_schema` from Pydantic instead of regex parsing:** Gemini's
  structured-output mode guarantees a JSON shape that matches the schema, so
  the same Pydantic model documents the contract and validates it, and
  brittle regex extraction over free-form text becomes unnecessary.
- **Why self-consistency instead of asking the model for confidence:**
  self-reported confidence proved unreliable in testing, since the model
  rated reconstructed values as confidently readable. Agreement across
  independent samples is a behavioral signal rather than a self-assessment:
  a genuine reading of a blurry digit is stable across runs, while a
  fabricated one differs each time, so disagreement exposes hallucination
  the model will not admit to. The cost is N API calls, issued concurrently
  so latency stays close to a single call.
- **Why one similarity score drives two opposite rules:** the same 0.90
  means different things for different fields, so `consensus.py` assigns
  each field a match policy. For an **address**, two readings 0.88 alike are
  the same address written with different form labels. Exact matching
  regressed a working card this way, dropping both address fields because
  three samples wrote `Sector No-10`, `Sector No.-10` and `Road: 05`. Those
  are merged. For a **number or date**, two readings 0.90 alike differ by
  one character, which is what a model produces when it is guessing at an
  ambiguous glyph, so the resemblance counts *against* the value and vetoes
  it.
- **Why a majority alone is unsafe for numbers and dates:** voting catches
  random fabrication, not systematic misreading. Measured on a deliberately
  degraded photo, three of five samples agreed on the same wrong date of
  birth, a clean majority for a value that was simply incorrect, while the
  two dissenters sat one digit away at 0.90. Under the strict policy that
  near-miss discards the field instead of confirming it. On the sharp
  original all ten fields still extract unanimously, so the rule costs
  nothing on good input.
- **Why nulls instead of errors for partial reads, but only up to a point:**
  A card photo often has one illegible field (e.g. a smudged NID number);
  failing the whole request would force needless re-uploads, so partial data
  is returned as a `200` with `null` for anything not confidently read. Past
  seven missing fields that reasoning inverts: the problem is the photo, not
  one field, and returning eight nulls invites the caller to build on a
  result that is barely there, so the request is rejected with a `422` and
  a retry instruction. The threshold is one constant,
  `config.MAX_UNREADABLE_FIELDS`, deliberately set to tolerate heavily
  partial reads and reject only a near-total failure; the per-field warning
  banner covers the milder cases.
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
offline, with no `GEMINI_API_KEY` required and no network call ever made.

## Project Structure

```
IDLCFinanceTakeHome/
├── app/
│   ├── main.py          # FastAPI app, routes, static file serving
│   ├── schemas.py        # Pydantic models (API response + Gemini schema)
│   ├── gemini_client.py  # Gemini call, prompt, retry logic
│   ├── consensus.py      # field-level agreement across samples
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
├── API.md                 # full endpoint documentation
├── AI_Usage.md            # how AI tools were used to build this
└── PROJECT.md             # requirements source of truth
```
