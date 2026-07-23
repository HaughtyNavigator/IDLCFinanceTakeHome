# API Documentation — Bangladesh NID Information Extractor

Version: 1.0.0

An HTTP API that extracts structured, English-language data from photos of a
Bangladesh National ID (NID) card. The caller uploads the front and back
images of the card; the service validates the images, sends them to Google
Gemini in a single structured-output call, and returns the card's fields as
JSON.

- **Base URL (local development):** `http://127.0.0.1:8000`
- **Interactive docs (Swagger UI):** `http://127.0.0.1:8000/docs`
- **OpenAPI schema:** `http://127.0.0.1:8000/openapi.json`
- **Authentication:** None. The service itself authenticates to Gemini with
  a server-side `GEMINI_API_KEY`; clients do not send credentials.

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves the static demo upload page (`static/index.html`). Not part of the JSON API and excluded from the OpenAPI schema. |
| `POST` | `/extract-nid` | Extracts structured NID data from front and back card images. |

---

## `POST /extract-nid`

Extract structured Bangladesh NID data from front and back card images.

### Request

- **Content type:** `multipart/form-data`
- **Form fields (both required):**

| Field | Type | Description |
|---|---|---|
| `front` | file | Photo of the **front** of the NID card. |
| `back` | file | Photo of the **back** of the NID card. |

**Upload constraints** (enforced server-side, in this order):

1. **Combined size limit:** `front` + `back` together must not exceed
   **10 MB** → otherwise `413`.
2. **Real image content:** each file must decode as a valid **JPEG or PNG**.
   Validation is done on the actual bytes with Pillow — the file extension
   and client-supplied `Content-Type` are ignored → otherwise `400`.
3. **Minimum resolution:** each image must be at least **300 px on each
   side** → otherwise `400`.

Before being sent to the AI model, each image is auto-rotated per its EXIF
orientation, downscaled so its longest side is at most **1600 px**, converted
to RGB, and re-encoded as JPEG. This is transparent to the caller.

### Example request

```bash
curl -X POST http://127.0.0.1:8000/extract-nid \
  -F "front=@nid_front.jpg" \
  -F "back=@nid_back.jpg"
```

Python:

```python
import requests

with open("nid_front.jpg", "rb") as f, open("nid_back.jpg", "rb") as b:
    resp = requests.post(
        "http://127.0.0.1:8000/extract-nid",
        files={"front": f, "back": b},
    )
print(resp.status_code, resp.json())
```

### Success response — `200 OK`

Content type `application/json`, always containing exactly these ten fields:

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

#### Field reference

Every field is `string | null`. Any field the model cannot read with
confidence is `null` — a partially filled response is still a `200`, and the
service never guesses or fabricates values.

A `200` is only returned while **at most four** of the ten fields are
`null`. At five or more the response is not worth acting on, so the service
returns a `422` asking for a better photo instead of a mostly empty object
(see the error table below).

| Field | Description | Normalization / validation |
|---|---|---|
| `name` | Card holder's full name, in English. | Bengali text is translated semantically using standard Bangladeshi romanization (e.g. `মোঃ` → `Md.`, `বেগম` → `Begum`). |
| `fatherName` | Father's full name. | Translated to English as above. |
| `motherName` | Mother's full name. | Translated to English as above. |
| `dateOfBirth` | Date of birth. | Must parse as an ISO `YYYY-MM-DD` date; any other format is replaced with `null`. |
| `bloodGroup` | Blood group as printed on the card. | Normalized (whitespace stripped, uppercased, Unicode minus → `-`). Must be one of `A+ A- B+ B- AB+ AB- O+ O-`; anything else becomes `null`. |
| `placeOfBirth` | Place of birth (usually a district). | Translated to English. |
| `nidNumber` | National ID number. | Whitespace and hyphens are stripped; the result must be exactly **10, 13, or 17 digits**, otherwise `null`. |
| `presentAddress` | Present address. | Translated to English. See address mirroring below. |
| `permanentAddress` | Permanent address. | Translated to English. See address mirroring below. |
| `issueDate` | Card issue date. | Must parse as an ISO `YYYY-MM-DD` date; any other format is replaced with `null`. |

**Address mirroring:** many NID layouts print only a single address. If
exactly one of `presentAddress` / `permanentAddress` is extracted, the value
is mirrored into the other field, so a single-address card never returns one
address field as `null`. The two fields differ only when the card explicitly
shows two distinct addresses.

**Layout notes:** both the older laminated paper NID and the newer smart-card
NID are supported. `bloodGroup`, `placeOfBirth`, and `issueDate` positions
vary by layout and are extracted from wherever they appear on either image.

### Error responses

Every error, regardless of cause, uses one JSON shape:

```json
{ "error": "<human-readable message>" }
```

| Status | Condition | Example message |
|---|---|---|
| `400` | A file is not a valid JPEG/PNG (corrupt, unsupported format, or not an image at all). | `Image file is corrupt or not a supported format (JPG/JPEG/PNG).` |
| `400` | An image is smaller than 300 px on either side. | `Image is too small (minimum 300px on each side). Please upload a higher-resolution photo.` |
| `413` | Combined upload exceeds 10 MB. | `Request too large (maximum 10 MB total).` |
| `422` | `front` and/or `back` form field is missing. | `Missing required file field(s): front, back` |
| `422` | The images are not a Bangladesh NID card. | `The uploaded images do not appear to be a Bangladesh NID card.` |
| `422` | Five or more of the ten fields could not be extracted consistently. | `The images were not clear enough to read reliably. Too few fields could be extracted consistently. Please upload sharper, well-lit photos of the NID card and try again.` — prefixed with the model's own readability complaint when it gave one. |
| `500` | Unexpected server error. | `Internal server error.` |
| `502` | The AI service failed, timed out, or returned an unusable response (after one automatic retry with a short backoff). | `AI service temporarily unavailable, please retry.` |
| `503` | The server has no `GEMINI_API_KEY` configured. This is an operator error, not a client error — retrying will not help until the variable is set. | `Server is not configured: GEMINI_API_KEY is not set. Set it in a .env file (see .env.example) or pass it to the container, then restart.` |

Notes:

- A readability problem alone does **not** cause an error: if the model
  flags the images as hard to read but at least six fields survive the
  agreement check, the request returns `200` with those fields and `null`
  elsewhere. The `422` readability error occurs only once five or more
  fields fail.
- The unreadable count is taken **after** address mirroring, so a card that
  prints a single address counts both address fields as readable.
- `502` is retryable by the client. The server already retries the AI call
  once internally, so a `502` means two consecutive attempts failed —
  waiting briefly before retrying is recommended.

### Status code summary

| Status | Meaning |
|---|---|
| `200` | Extraction succeeded (with at most four `null` fields). |
| `400` | Invalid image upload (format or resolution). |
| `413` | Upload too large. |
| `422` | Missing file field, not an NID card, or too few readable fields. |
| `500` | Unexpected server error. |
| `502` | AI service unavailable. |
| `503` | Server misconfigured — `GEMINI_API_KEY` not set. |

---

## Operational behavior

- **Processing model:** each request runs `EXTRACTION_SAMPLES` independent
  Gemini structured-output calls (default 3, plus at most one internal retry
  each on transient failure). The samples are issued concurrently, so
  latency stays close to that of a single call even though several are made.
  Only fields that agree across a majority of samples are returned; the rest
  are `null`. If every sample fails, the request returns `502`; if some
  succeed, the consensus is taken over those. The configured per-call
  timeout is 60 seconds.
- **Why several samples:** a value the model genuinely read is stable across
  runs, while one it reconstructed from an unclear image differs each time.
  Disagreement is therefore treated as an unreadable field. The sampling
  temperature is deliberately left at the model default — at temperature
  zero every sample would be identical and the check would detect nothing.
- **Consensus logging:** each request logs one line per field showing how
  many samples backed the winning reading and whether it was kept, dropped
  as inconsistent, or never read at all. The values themselves are omitted
  unless `CONSENSUS_LOG_VALUES` is explicitly enabled — see below.
- **PII safety:** the service logs only request metadata — method, path,
  status code, duration, a coarse error category, and the per-field vote
  counts above. Image bytes, extracted field values, and the Gemini API key
  are never logged and never appear in error messages.
- **Statelessness:** nothing is persisted. Uploaded images and extracted
  data exist only in memory for the duration of the request.

## Configuration (server-side)

Set via environment variables / `.env` (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | — (required) | Google Gemini API key. If unset, the server still starts but logs a critical startup error and fails extraction requests with `503`. |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Gemini model used for extraction. |
| `EXTRACTION_SAMPLES` | `3` | How many independent extractions to run per request. Fields must agree across a majority of samples to be returned. `1` disables consensus. |
| `CONSENSUS_LOG_VALUES` | `false` | Local debugging only. Include the extracted values, and the competing readings for a disputed field, in the consensus log lines. Those values are personal data, so leave this off anywhere logs are collected, shipped, or shared. Only an explicit `1`/`true`/`yes`/`on` enables it. |

Example of the per-field log emitted for one request (default settings, so
values are hidden):

```
consensus over 3 sample(s), 2 must agree (values hidden, set CONSENSUS_LOG_VALUES=true to show)
  name              DROPPED 1/3  samples disagreed
  fatherName        KEPT    3/3  majority agreed
  placeOfBirth      EMPTY   0/3  no sample read this field
  ...
  is_nid            KEPT    3/3  recognised as an NID card
consensus result: 6/10 field(s) kept
```

Fixed limits (in `app/config.py`): 10 MB combined upload, 300 px minimum
side, 1600 px maximum longest side, 60 s Gemini timeout, one retry after a
2 s delay, and `MAX_UNREADABLE_FIELDS = 4` — the most `null` fields a `200`
may contain before the extraction is rejected as an unusable photo.
