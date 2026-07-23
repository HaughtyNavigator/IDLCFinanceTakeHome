"""Field-level agreement across several independent extractions.

A model reading a genuinely legible field returns the same value every time,
while a value it reconstructed from a blurry image tends to differ between
runs. Comparing several independent extractions therefore exposes fabricated
values that the model itself reports as readable: any field that does not
agree across a majority of samples is discarded.

The voting rules themselves are pure — they perform no I/O and make no API
calls — so they can be tested directly. The module only writes log lines
describing the decisions it made.
"""

import logging
import re
from collections import Counter

from app import config
from app.schemas import GeminiNIDExtraction, NIDData

logger = logging.getLogger("nid_extractor.consensus")

PUBLIC_FIELD_NAMES: tuple[str, ...] = tuple(NIDData.model_fields)

_WHITESPACE_PATTERN = re.compile(r"\s+")
_INSIGNIFICANT_EDGE_CHARS = " \t\r\n.,;:-"
_FIELD_LABEL_WIDTH = max(len(name) for name in PUBLIC_FIELD_NAMES) + 1
_MAX_LOGGED_VALUE_CHARS = 60


def minimum_agreement(sample_count: int) -> int:
    """Return how many samples must agree for a value to be accepted.

    A simple majority: 1 of 1, 2 of 2, 2 of 3, 3 of 4, 3 of 5.
    """
    return (sample_count // 2) + 1


def _comparison_key(value: str) -> str:
    """Canonical form used only for comparing values, never for output.

    Collapses whitespace, folds case, and ignores trailing punctuation so
    that cosmetic differences between runs ("Md. Rahim" vs "Md Rahim") count
    as agreement rather than as a contradiction.
    """
    collapsed = _WHITESPACE_PATTERN.sub(" ", value).strip()
    return collapsed.casefold().strip(_INSIGNIFICANT_EDGE_CHARS)


def _tally(values: list[str | None]) -> tuple[str | None, int, list[str]]:
    """Count the samples for a single field.

    Returns:
        A tuple of the leading value (the most common original spelling among
        the samples that match the winning form), how many samples backed it,
        and the list of non-empty values that were read. The leading value is
        returned regardless of whether it reaches any threshold; applying the
        threshold is the caller's job.
    """
    present = [value for value in values if value is not None and value.strip()]
    if not present:
        return None, 0, present

    key_counts = Counter(_comparison_key(value) for value in present)
    winning_key, agreement = key_counts.most_common(1)[0]
    matching = [value for value in present if _comparison_key(value) == winning_key]
    return Counter(matching).most_common(1)[0][0], agreement, present


def vote(values: list[str | None], min_agreement: int) -> str | None:
    """Return the agreed value, or None if the samples do not agree.

    Args:
        values: One extracted value per sample; None where a sample did not
            read the field at all.
        min_agreement: How many samples must produce the same value.

    Returns:
        The most common original spelling among the agreeing samples, or
        None when no value reaches the agreement threshold. A field that
        most samples left empty therefore stays empty.
    """
    leader, agreement, _ = _tally(values)
    if leader is None or agreement < min_agreement:
        return None
    return leader


def _redact(value: str) -> str:
    """Format one extracted value for a log line.

    Only ever called when value logging has been explicitly enabled; the
    length cap keeps a long address from dominating the terminal output.
    """
    collapsed = _WHITESPACE_PATTERN.sub(" ", value).strip()
    if len(collapsed) > _MAX_LOGGED_VALUE_CHARS:
        collapsed = collapsed[: _MAX_LOGGED_VALUE_CHARS - 1] + "…"
    return f"'{collapsed}'"


def _describe_candidates(present: list[str]) -> str:
    """Describe what the samples read for one field, most common first."""
    if not present:
        return ""
    counts = Counter(present)
    return "  candidates: " + ", ".join(
        f"{_redact(value)} x{count}" for value, count in counts.most_common()
    )


def _log_field_decision(
    field_name: str,
    leader: str | None,
    agreement: int,
    present: list[str],
    sample_count: int,
    min_agreement: int,
    show_values: bool,
) -> None:
    """Log one line explaining how a single field was decided."""
    if leader is None:
        verdict = "EMPTY  "
        detail = "no sample read this field"
    elif agreement >= min_agreement:
        verdict = "KEPT   "
        detail = _redact(leader) if show_values else "majority agreed"
    else:
        verdict = "DROPPED"
        detail = "samples disagreed"

    line = "  %s %s %d/%d  %s" % (
        field_name.ljust(_FIELD_LABEL_WIDTH),
        verdict,
        agreement,
        sample_count,
        detail,
    )
    if show_values and leader is not None and agreement < sample_count:
        # Only interesting when the samples were not unanimous.
        line += _describe_candidates(present)
    logger.info(line)


def _agreed_readability_issue(
    extractions: list[GeminiNIDExtraction],
) -> str | None:
    """Return the first readability complaint reported by any sample."""
    for extraction in extractions:
        if extraction.readability_issue:
            return extraction.readability_issue
    return None


def build_consensus(
    extractions: list[GeminiNIDExtraction], min_agreement: int
) -> GeminiNIDExtraction:
    """Merge several extractions into one, keeping only agreed values.

    Args:
        extractions: Successful extractions of the same pair of images.
        min_agreement: How many samples must agree per field.

    Returns:
        A single extraction whose fields are the agreed values, with
        disagreeing fields set to None.

    Logs one line per field showing how many samples backed the winning
    reading and whether it was kept. Extracted values appear only when
    ``CONSENSUS_LOG_VALUES`` is enabled, since they are personal data.
    """
    sample_count = len(extractions)
    show_values = config.consensus_log_values()
    logger.info(
        "consensus over %d sample(s), %d must agree%s",
        sample_count,
        min_agreement,
        (
            ""
            if show_values
            else " (values hidden, set CONSENSUS_LOG_VALUES=true to show)"
        ),
    )

    agreed: dict[str, str | None] = {}
    for field_name in PUBLIC_FIELD_NAMES:
        leader, agreement, present = _tally(
            [getattr(extraction, field_name) for extraction in extractions]
        )
        accepted = leader if agreement >= min_agreement else None
        agreed[field_name] = accepted
        _log_field_decision(
            field_name,
            leader,
            agreement,
            present,
            sample_count,
            min_agreement,
            show_values,
        )

    nid_votes = sum(1 for extraction in extractions if extraction.is_nid)
    kept = sum(1 for value in agreed.values() if value is not None)
    logger.info(
        "  %s %s %d/%d  %s",
        "is_nid".ljust(_FIELD_LABEL_WIDTH),
        "KEPT   " if nid_votes >= min_agreement else "DROPPED",
        nid_votes,
        sample_count,
        (
            "recognised as an NID card"
            if nid_votes >= min_agreement
            else "not recognised as an NID card"
        ),
    )
    logger.info("consensus result: %d/%d field(s) kept", kept, len(PUBLIC_FIELD_NAMES))

    return GeminiNIDExtraction(
        **agreed,
        is_nid=nid_votes >= min_agreement,
        readability_issue=_agreed_readability_issue(extractions),
    )
