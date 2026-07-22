# Development Log

## 2026-07-18
- Project setup: created `.venv` (Python 3.13.2), installed and pinned dependencies in `requirements.txt` (fastapi, uvicorn, google-genai, pydantic v2, pillow, python-dotenv, python-multipart; pytest + httpx for tests).
- `python-multipart` added beyond the spec list: required by FastAPI to parse `multipart/form-data` uploads. `httpx` added as pytest dependency for FastAPI's TestClient.
- Created `.env` (empty `GEMINI_API_KEY`, gitignored), `.env.example`, and `.gitignore` (covers `.env`, `__pycache__/`, image files per git hygiene rules).
- Verified `gemini-3.1-flash-lite` (the model fixed in PROJECT.md) is available on the configured API key via a models.list call.
- Added `black`/`isort` as pinned dev deps: CLAUDE.md mandates black formatting + isort imports, so the tools belong in requirements.
- User-supplied sample NID images placed in `NID_Images/` (per person, front=page-0001/back=page-0002); added `NID_Images/` to `.gitignore` — real NID cards must never be committed.
- Implemented `app/schemas.py` + `app/config.py` (subagent): NIDData public contract, GeminiNIDExtraction (adds is_nid/readability_issue, inherits validators so Gemini output is re-validated), invalid nidNumber/dateOfBirth become null rather than errors.
- Mistake (orchestrator): acceptance example claimed '123 4567890' was 7 digits — it is 10, so it validly passes; subagent flagged the miscount and correctly followed the spec algorithm instead of the bad example.
- Implemented `app/image_utils.py` (subagent): byte-level Pillow verification, JPEG/PNG only, min-300px check, EXIF transpose, LANCZOS downscale to 1600px, RGB JPEG q85 re-encode. All 6 acceptance scenarios passed.
- Implemented `static/index.html` (subagent): vanilla JS upload UI with previews, loading state, key/value result panel (nulls shown as "Not readable"), raw-JSON details, error banner, aria-live region, no external resources.
- Implemented `app/gemini_client.py` + `app/main.py` (subagent): single structured-output Gemini call with one retry on transient errors, all error paths mapped to the JSON `{"error": ...}` shape (400/413/422/502/500), metadata-only request logging, sync endpoint so the blocking Gemini call runs in FastAPI's threadpool. 9/9 smoke scenarios passed with mocked client.
- Review fix (orchestrator): added missing `GeminiNIDExtraction` type hint on `_resolve_extraction_outcome` in main.py.
- Implemented `tests/test_api.py` + root `conftest.py` + `README.md` (subagent): 11 tests, all mocked at `app.main.extract_nid_data` — no real API calls, no key needed.
- Final review pass: black/isort clean across app+tests; `pytest` → 11 passed (1 unrelated Starlette deprecation warning from FastAPI internals); server boots, `/` and `/docs` serve 200.
- End-to-end with real sample images: Person_1 → 200 in ~2.9s, six fields extracted, permanentAddress correctly null (partial success); Person_2 → 200 in ~4.4s, all seven fields extracted. Live error paths verified: missing `back` → 422 naming the field, corrupt bytes → 400 with exact spec message.
- Confirmed server logs contain metadata only (method/path/status/latency/category) — no PII, image bytes, or key. Project complete per Definition of Done.
- User change request: extend contract with bloodGroup, placeOfBirth, issueDate; fix result panel showing "Not readable" for populated fields; add JSON download named `<extracted_name>.json`.
- Bug root cause (frontend subagent's original work): `FIELD_LABELS` used snake_case keys while the API returns camelCase — only `name` matched. Fixed to camelCase; would have been caught earlier by an end-to-end check of the rendered UI, not just the API.
- Backend subagent: 3 new fields with validators (blood group normalized against the 8 valid groups, issueDate shares the ISO-date validator with dateOfBirth), prompt updated for layout-dependent field placement, tests now 13 (added invalid/messy blood-group cases), README example updated to 10 fields.
- Frontend subagent: 10-field label list, ghost-style "Download JSON" button saving `<sanitized name>.json` (fallback `nid-extraction.json`).
- Verified: 13/13 tests pass; live e2e — Person_1 and Person_2 both 200 with bloodGroup/placeOfBirth/issueDate populated; Person_1 permanentAddress remains null (consistent with earlier runs).
- User report: single-address cards inconsistently yielded null permanentAddress (model judgment call varied per request). Fix (subagent): `model_validator(mode="after")` on NIDData mirrors a lone address into the empty field — deterministic guarantee in code, plus a matching prompt instruction. 3 new tests (present-only, permanent-only, distinct-both preserved); 16 passed.
- Design note: mirroring implemented symmetrically (works in either direction), on the reasoning that a single printed address is the card's only address of record.
- Verified live: Person_1 permanentAddress now mirrors presentAddress; Person_2 unchanged (both addresses identical as before).

## 2026-07-22
- Requirements audit against the company brief: all functional/technical items covered, but found `docker compose up` hard-failed on a fresh clone — `env_file: .env` is mandatory in Compose and `.env` is (correctly) gitignored, so an evaluator cloning the repo hit `env file ... not found`. Verified by running compose in an empty directory.
- Fix: `env_file` now `required: false` plus an `environment:` passthrough, so the app runs from `.env` OR from `GEMINI_API_KEY=... docker compose up` with no file at all. Verified all three cases (fresh clone parses, shell var wins, existing `.env` still resolves and is not clobbered by the empty default).
- Security review of that change: no new exposure — key stays gitignored, stays out of the image, still runtime-injected; `docker inspect` reveals env identically under either mechanism. Real delta is fail-closed becoming fail-open, addressed below.
- Fix: missing key previously surfaced as 502 "AI service temporarily unavailable" — misleading, reads as an upstream outage rather than an operator error. Added `GeminiConfigurationError(GeminiServiceError)` and a dedicated handler returning 503 naming the variable, plus a critical log line at startup via a lifespan hook.
- Decision: startup check logs and continues rather than exiting. Compose sets `restart: unless-stopped`, so exiting on boot would crash-loop — worse for an evaluator than the failure it replaces.
- Cleanup: untracked `this is not an image` (stray corrupt-upload fixture) and `.claude/settings.local.json` (machine-local paths); added `.claude/` to `.gitignore`.
- Tests now 17 (added missing-key 503 case); black/isort clean. `PROJECT.md` and `CLAUDE (2).md` intentionally kept in the repo at the user's request as evidence of process.

## 2026-07-19
- Dockerized the app (subagent): `Dockerfile` (python:3.13-slim, layered pip install, copies only `app/` + `static/`, non-root user, stdlib HEALTHCHECK), `.dockerignore` (excludes `.env`, `NID_Images/`, `.venv/` — secrets/PII never enter the image), `docker-compose.yml` (`env_file: .env`, port 8000), README "Run with Docker" section.
- Review fix (orchestrator): `.dockerignore` patterns `__pycache__/`/`*.pyc` only match at context root; changed to `**/__pycache__/`/`**/*.pyc` after finding stale host bytecode inside the built image.
- Verified in container: image has no `.env`/`NID_Images` and runs as `appuser`; `docker compose up --build` → healthy, GET / and /docs 200, missing-back → 422, full extraction on Person_1 → 200 in ~2.8s with all 10 fields; plain `docker run --env-file .env` path also verified; container logs metadata-only (no PII). Image size 341 MB.
- README checklist pass (orchestrator, docs-only): added explicit "Technology Stack" table and "AI / OCR Approach" sections — previously only covered implicitly in prose — so every item in the company's README checklist maps to a dedicated heading.
