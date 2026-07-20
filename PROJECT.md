# Project: Bangladesh NID Information Extractor

## Overview
An AI-powered web application that extracts information from both sides of a Bangladesh National ID (NID) card and returns the data in English as structured JSON. Built as a demonstration project for a fintech role interview — code must be clean, explainable, and production-minded, but scoped small (~150–300 lines total backend).

## Tech Stack (fixed — do not substitute)
- **Language:** Python 3.11+
- **Framework:** FastAPI (with Uvicorn)
- **AI Model:** Google Gemini 3.1 Flash Lite via the official `google-genai` SDK — used as a single multimodal call (OCR + translation + structuring in one step). Do NOT use Tesseract or any separate OCR/translation library.
- **Validation:** Pydantic v2 (shared between API response contract and Gemini structured-output schema)
- **Image handling:** Pillow
- **Frontend:** One static HTML page (vanilla JS, no framework) served by FastAPI, with two file inputs (front/back), a submit button, a loading state, and a formatted JSON result panel.
- **Config:** API key loaded from environment variable `GEMINI_API_KEY` via `python-dotenv`. Never hardcode keys.

## API Design

### `POST /extract-nid`
- Accepts `multipart/form-data` with two required file fields: `front` and `back`.
- Accepted formats: JPG, JPEG, PNG (validate by actual file bytes with Pillow, not by extension alone).
- Returns `200` with the JSON schema below on success.

### Success response schema
```json
{
  "name": "Md. Rahim",
  "fatherName": "Abdul Karim",
  "motherName": "Amena Begum",
  "dateOfBirth": "1998-01-15",
  "nidNumber": "1234567890123",
  "presentAddress": "Dhaka, Bangladesh",
  "permanentAddress": "Cumilla, Bangladesh"
}
```
- Field names must be exactly these (camelCase).
- `dateOfBirth` normalized to ISO 8601 (YYYY-MM-DD).
- `nidNumber` must be 10, 13, or 17 digits; validate with Pydantic.
- Any field that cannot be confidently extracted is returned as `null` (partial extraction is a valid success case, not an error).

### Gemini integration requirements
- Single API call containing BOTH images plus one instruction prompt.
- Use structured output: `response_mime_type="application/json"` with a `response_schema` derived from the Pydantic model. Do not parse free-form text with regex.
- The internal Gemini schema should include two extra fields not exposed in the final API response:
  - `is_nid` (bool) — whether the images actually appear to be a Bangladesh NID card
  - `readability_issue` (string | null) — short description if images are blurry/unreadable
- Prompt must instruct the model to:
  1. Extract all fields from front (name, father's name, mother's name, DOB, NID number) and back (addresses).
  2. Translate Bengali fields to English **semantically**, using standard Bangladeshi romanization conventions for names (e.g., "মোঃ" → "Md.", "বেগম" → "Begum"), not literal word-for-word translation.
  3. Normalize DOB to YYYY-MM-DD.
  4. Return `null` for any field it cannot read confidently — never guess or hallucinate values.

## Error Handling (all responses JSON: `{"error": "<message>"}` plus appropriate status code)
| Condition | Status | Behavior |
|---|---|---|
| Missing front or back file | 422 | Clear message naming the missing field |
| Wrong format / corrupt image (Pillow verify fails) | 400 | "Image file is corrupt or not a supported format (JPG/JPEG/PNG)." |
| Image too small (< 300px on either dimension) | 400 | Message suggesting a higher-resolution photo |
| `is_nid` is false | 422 | "The uploaded images do not appear to be a Bangladesh NID card." |
| `readability_issue` set and all fields null | 422 | Surface the readability issue to the user |
| Gemini API failure / timeout | 502 | "AI service temporarily unavailable, please retry." One automatic retry with backoff before failing. |
| Partial extraction (some fields null) | 200 | Return data as-is with nulls; frontend displays "Not readable" for null fields |

## Non-Functional Requirements
- **PII safety (fintech context):** never log image bytes or extracted personal data. Log only request metadata (timestamp, status, latency, error category).
- Downscale images to max 1600px on the longest side before sending to Gemini (cost/latency).
- Type hints everywhere; docstrings on public functions.
- Auto-generated Swagger docs at `/docs` must work as a demo interface.
- Include a `README.md` with setup steps (`pip install -r requirements.txt`, set `GEMINI_API_KEY`, `uvicorn app.main:app --reload`) and a short architecture explanation.

## Project Structure
```
nid-extractor/
├── app/
│   ├── main.py          # FastAPI app, routes, static file serving
│   ├── schemas.py       # Pydantic models (API response + Gemini schema)
│   ├── gemini_client.py # Gemini call, prompt, retry logic
│   ├── image_utils.py   # validation, verify, downscale
│   └── config.py        # env/config loading
├── static/
│   └── index.html       # upload UI
├── tests/
│   └── test_api.py      # basic tests: missing file, bad format, mocked happy path
├── requirements.txt
├── .env.example
├── README.md
└── LOG.md               # development journal (see CLAUDE.md)
```

## Out of Scope
- Authentication, rate limiting, databases, Docker, deployment configs.
- Any frontend framework or build tooling.
- Supporting smart-card NID layouts beyond a best-effort prompt (the prompt should mention both old paper and newer smart NID layouts exist).

## Definition of Done
1. All five functional requirements met (upload, read, translate, structured JSON, error handling).
2. `pytest` passes.
3. App runs locally with a single `uvicorn` command and works end-to-end from the HTML page and from `/docs`.
4. Every design decision is explainable in one or two sentences (this is an interview demo).
