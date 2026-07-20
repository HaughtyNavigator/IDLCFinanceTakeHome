# CLAUDE.md

Guidance for Claude Code when working in this repository. Read PROJECT.md first — it is the source of truth for scope, stack, and requirements. Do not deviate from the stack defined there without asking.

## Orchestration Model
- **Fable (this session) acts as orchestrator only.** It plans, decomposes tasks, reviews output, and maintains the log. It does not write application code directly.
- **All code is written by Sonnet subagents.** Dispatch implementation work (routes, schemas, Gemini client, image utils, frontend, tests) to Sonnet subagents via the Task tool, with a tight, self-contained brief per task: relevant file paths, acceptance criteria, and constraints from PROJECT.md.
- Orchestrator responsibilities: task breakdown, sequencing, reviewing subagent diffs against PROJECT.md, resolving conflicts between subagent outputs, running tests, and updating LOG.md.
- Keep subagent tasks small and parallelizable where files don't overlap (e.g., `schemas.py` and `image_utils.py` can be built concurrently; `main.py` waits on both).

## Development Log (LOG.md)
- Maintain `LOG.md` at the repo root as a running journal.
- Append a short entry (1–3 lines is fine — this is a notebook, not documentation) whenever any of the following happens:
  - A feature or file is completed
  - A mistake is made and corrected (note what went wrong and the fix)
  - A design decision or deviation is made
  - Tests fail and why
- Format:
  ```
  ## 2026-07-18
  - Implemented schemas.py; NID number validator accepts 10/13/17 digits.
  - Mistake: initially validated file type by extension only; fixed to Pillow byte verification per spec.
  ```
- Newest entries at the bottom. Never delete or rewrite past entries.

## Code Standards
- Python 3.11+, PEP 8, formatted with `black` (default settings), imports sorted with `isort`.
- Type hints on all function signatures. Pydantic v2 idioms (no deprecated v1 patterns).
- Docstrings on all public functions/classes (one-liners are fine for simple helpers).
- No function longer than ~40 lines; extract helpers instead.
- Explicit exception handling: catch specific exceptions, never bare `except:`. Every error path returns the JSON error shape defined in PROJECT.md.
- No dead code, no commented-out blocks, no TODO left behind at completion.
- Prefer standard library over adding dependencies. Any new dependency must be justified in LOG.md and added to `requirements.txt` with a pinned version.

## Security & Secrets
- Never hardcode API keys, tokens, or credentials. All secrets come from environment variables; `.env` is gitignored, `.env.example` documents required vars with placeholder values.
- Never log, print, or persist uploaded image bytes or extracted PII (names, addresses, NID numbers, DOB). Logs contain metadata only: timestamp, route, status code, latency, error category.
- Validate all user input at the boundary (file presence, byte-level format check, size limits) before it touches the AI client.
- Set a request body size limit (10 MB total) to prevent abuse.

## Testing
- `pytest` for all tests. Mock the Gemini client in tests — tests must never make real API calls or require a key.
- Minimum coverage: missing-file error, corrupt-image error, non-NID rejection, mocked happy path returning the exact JSON contract, partial-extraction (nulls) case.
- Run the full test suite after every subagent task is merged; a task is not done until tests pass.

## Git Hygiene
- Small, atomic commits with imperative messages ("Add image validation", not "added stuff").
- Never commit `.env`, `__pycache__/`, or test images containing real NID cards. `.gitignore` must cover these from the first commit.

## Workflow Rules
- Before writing code for a task, restate its acceptance criteria; after, verify against them.
- If PROJECT.md is ambiguous or two requirements conflict, stop and ask the user rather than guessing. Record the resolution in LOG.md.
- After completing the project, do a final review pass: run tests, start the server, confirm `/docs` works, and write a closing LOG.md entry summarizing what was built.
