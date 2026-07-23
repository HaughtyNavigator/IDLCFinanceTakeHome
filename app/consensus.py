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
from difflib import SequenceMatcher
from enum import Enum
from typing import NamedTuple

from app import config
from app.schemas import GeminiNIDExtraction, NIDData

logger = logging.getLogger("nid_extractor.consensus")

PUBLIC_FIELD_NAMES: tuple[str, ...] = tuple(NIDData.model_fields)


class MatchPolicy(Enum):
    """How two readings of the same field are compared.

    The same similarity score is read in opposite directions depending on
    the field. For an address, two readings 0.88 alike are the same address
    written with different form labels, so they should be merged. For a
    ten-digit number, two readings 0.90 alike differ by one character, which
    is the signature of the model guessing at an ambiguous glyph — there the
    resemblance is evidence against the value, not for it.
    """

    EXACT = "exact"
    FUZZY = "fuzzy"
    STRICT = "strict"


FIELD_MATCH_POLICIES: dict[str, MatchPolicy] = {
    # Long free text; the model picks its own rendering of the printed form
    # labels, so near-identical readings are merged rather than counted as
    # disagreement.
    "presentAddress": MatchPolicy.FUZZY,
    "permanentAddress": MatchPolicy.FUZZY,
    # Machine-readable values where one wrong character makes the whole
    # field wrong and silently unusable downstream. A simple majority is not
    # enough here: on a degraded photo the samples can converge on the same
    # misread digit, so a near-miss dissent vetoes the value outright.
    "nidNumber": MatchPolicy.STRICT,
    "dateOfBirth": MatchPolicy.STRICT,
    "issueDate": MatchPolicy.STRICT,
    "bloodGroup": MatchPolicy.STRICT,
}

# Chosen against real samples of the same card: readings that differed only
# in label wording scored 0.88, so 0.85 accepts them with margin to spare.
FUZZY_MATCH_RATIO = 0.85

# A dissenting reading this similar to the winner is treated as a dispute
# over a character rather than an unrelated misread. Measured on real
# degraded images: one wrong NID digit scores 0.92, a date one digit out
# scores 0.90, and heavily corrupted NID readings still score 0.80. Below
# this the dissent is unrelated to the winner (a sample that read a
# different part of the card, say), which is not evidence that the winning
# reading is ambiguous.
NEAR_MISS_RATIO = 0.70


def policy_for(field_name: str) -> MatchPolicy:
    """Return the comparison policy for a public field."""
    return FIELD_MATCH_POLICIES.get(field_name, MatchPolicy.EXACT)


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


def _similarity(left: str, right: str) -> float:
    """Return how alike two comparison keys are, from 0.0 to 1.0."""
    # autojunk would start discarding frequent characters on longer inputs,
    # which for an address is exactly the content being compared.
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _representative(cluster: list[str]) -> str:
    """Pick the value that best stands for a group of near-identical ones.

    The most common spelling wins. When every member is distinct — the usual
    case for addresses — the tie is broken by picking the member closest to
    all the others, so the returned reading is the middle one rather than an
    outlier that happens to sort first.
    """
    counts = Counter(cluster)
    top_count = counts.most_common(1)[0][1]
    candidates = [value for value in counts if counts[value] == top_count]
    if len(candidates) == 1:
        return candidates[0]

    keys = {value: _comparison_key(value) for value in cluster}
    return max(
        candidates,
        key=lambda value: sum(
            _similarity(keys[value], keys[other]) for other in cluster
        ),
    )


class Tally(NamedTuple):
    """The outcome of counting one field across the samples."""

    leader: str | None
    agreement: int
    present: list[str]
    near_miss_ratio: float | None = None
    """Set under STRICT when a dissenting reading is close enough to the
    winner to indicate a disputed character, which vetoes the value."""


def _find_near_miss(winning_key: str, keys: list[str]) -> float | None:
    """Return the score of the closest dissenting reading, if it is a near miss.

    A dissent that resembles the winner means the samples disagree about a
    character rather than about the whole value — exactly what happens when
    a blurred digit reads as two different digits on different runs.
    """
    scores = [
        _similarity(winning_key, key) for key in keys if key and key != winning_key
    ]
    closest = max(scores, default=0.0)
    return closest if closest >= NEAR_MISS_RATIO else None


def _tally(values: list[str | None], policy: MatchPolicy = MatchPolicy.EXACT) -> Tally:
    """Count the samples for a single field.

    Args:
        values: One extracted value per sample, or None where a sample did
            not read the field.
        policy: How readings are compared; see MatchPolicy.

    Returns:
        A Tally holding the leading value (the most common original spelling
        among the samples matching the winning form), how many samples
        backed it, the non-empty values that were read, and — under STRICT —
        the score of any near-miss dissent. Thresholds are the caller's job.
    """
    present = [value for value in values if value is not None and value.strip()]
    if not present:
        return Tally(None, 0, present)

    keys = [_comparison_key(value) for value in present]

    if policy is MatchPolicy.FUZZY:
        best: list[str] = []
        for key in keys:
            cluster = [
                value
                for value, other in zip(present, keys)
                if other == key or _similarity(key, other) >= FUZZY_MATCH_RATIO
            ]
            if len(cluster) > len(best):
                best = cluster
        return Tally(_representative(best), len(best), present)

    winning_key, agreement = Counter(keys).most_common(1)[0]
    matching = [value for value, key in zip(present, keys) if key == winning_key]
    leader = Counter(matching).most_common(1)[0][0]

    near_miss = None
    if policy is MatchPolicy.STRICT:
        near_miss = _find_near_miss(winning_key, keys)
    return Tally(leader, agreement, present, near_miss)


def vote(
    values: list[str | None],
    min_agreement: int,
    *,
    policy: MatchPolicy = MatchPolicy.EXACT,
) -> str | None:
    """Return the agreed value, or None if the samples do not agree.

    Args:
        values: One extracted value per sample; None where a sample did not
            read the field at all.
        min_agreement: How many samples must produce the same value.
        policy: How readings are compared; see MatchPolicy.

    Returns:
        The most common original spelling among the agreeing samples, or
        None when no value reaches the agreement threshold, or — under
        STRICT — when a dissenting sample read the value slightly
        differently. A field that most samples left empty stays empty.
    """
    tally = _tally(values, policy)
    if tally.leader is None or tally.agreement < min_agreement:
        return None
    if tally.near_miss_ratio is not None:
        return None
    return tally.leader


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
    policy: MatchPolicy = MatchPolicy.EXACT,
    near_miss_ratio: float | None = None,
) -> None:
    """Log one line explaining how a single field was decided."""
    if leader is None:
        verdict = "EMPTY  "
        detail = "no sample read this field"
    elif near_miss_ratio is not None:
        verdict = "DROPPED"
        detail = f"a sample read it {near_miss_ratio:.2f} alike but not equal"
    elif agreement >= min_agreement:
        verdict = "KEPT   "
        detail = _redact(leader) if show_values else "majority agreed"
        if policy is MatchPolicy.FUZZY:
            detail += " (near-matches counted)"
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
        policy = policy_for(field_name)
        tally = _tally(
            [getattr(extraction, field_name) for extraction in extractions],
            policy,
        )
        accepted = tally.leader
        if tally.agreement < min_agreement or tally.near_miss_ratio is not None:
            accepted = None
        agreed[field_name] = accepted
        _log_field_decision(
            field_name,
            tally.leader,
            tally.agreement,
            tally.present,
            sample_count,
            min_agreement,
            show_values,
            policy=policy,
            near_miss_ratio=tally.near_miss_ratio,
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
