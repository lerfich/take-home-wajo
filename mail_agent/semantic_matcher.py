"""Small, deterministic matcher for model-produced semantic contexts.

The model is responsible for extracting a bounded ``meaning``/``subtopic``.  This
module deliberately does not infer intent from an address or a subject line.  It
only compares the extracted meaning first and uses the other fields to explain
and rank otherwise compatible candidates.
"""

from dataclasses import asdict, dataclass
import re
from typing import Any, Mapping


_WORDS = re.compile(r"[\w]+", re.UNICODE)


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(_WORDS.findall(value.casefold()))


def _tokens(value: str) -> set[str]:
    return {word for word in _clean(value).split() if len(word) > 1}


def _overlap(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass(frozen=True)
class SemanticContext:
    meaning: str
    subtopic: str = ""
    subject: str = ""
    sender: str = ""
    evidence: str = ""
    ambiguous: bool = False
    suspicious: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MatchResult:
    outcome: str
    score: float
    reason: str
    signals: tuple[str, ...] = ()

    @property
    def matched(self) -> bool:
        return self.outcome == "match"


def coerce_context(value: SemanticContext | Mapping[str, Any] | Any) -> SemanticContext:
    """Accept stable mappings, dataclasses and Proposal-like objects.

    The aliases are adapter hooks for the existing Skills model and the Events
    extractor.  Unknown free text is not promoted to a semantic meaning.
    """
    if isinstance(value, SemanticContext):
        return value
    if not isinstance(value, Mapping):
        value = vars(value) if hasattr(value, "__dict__") else {}
    meaning = (value.get("meaning") or value.get("semantic_kind") or
               value.get("label_kind") or value.get("pattern") or "")
    confidence = value.get("confidence", "clear")
    return SemanticContext(
        meaning=str(meaning),
        subtopic=str(value.get("subtopic") or value.get("event_type") or value.get("kind") or ""),
        subject=str(value.get("subject") or ""),
        sender=str(value.get("sender") or ""),
        evidence=str(value.get("evidence") or value.get("original_text") or value.get("pattern_evidence") or ""),
        ambiguous=bool(value.get("ambiguous", False) or confidence == "ambiguous"),
        suspicious=bool(value.get("suspicious", False)),
    )


def from_email_proposal(email: Any, proposal: Any, *, meaning: str = "", subtopic: str = "") -> SemanticContext:
    """Bridge current Email/Proposal objects to the shared matcher."""
    data = vars(proposal) if hasattr(proposal, "__dict__") else dict(proposal)
    return SemanticContext(
        meaning=meaning or str(data.get("semantic_kind") or data.get("label_kind") or data.get("pattern") or ""),
        subtopic=subtopic or str(data.get("subtopic") or ""),
        subject=str(getattr(email, "subject", "")), sender=str(getattr(email, "sender", "")),
        evidence=str(data.get("evidence") or data.get("pattern_evidence") or ""),
        ambiguous=bool(data.get("ambiguous", False)), suspicious=bool(data.get("suspicious", False)),
    )


def compare(saved: SemanticContext | Mapping[str, Any], candidate: SemanticContext | Mapping[str, Any],
            *, threshold: float = 0.65) -> MatchResult:
    """Compare semantic meaning first; supplemental fields can never replace it."""
    left, right = coerce_context(saved), coerce_context(candidate)
    if left.suspicious or right.suspicious:
        return MatchResult("unknown", 0.0, "Safety review is required; context does not grant authority.")
    if left.ambiguous or right.ambiguous:
        return MatchResult("unknown", 0.0, "The event meaning is ambiguous and needs confirmation.")
    left_meaning, right_meaning = _clean(left.meaning), _clean(right.meaning)
    if right_meaning == "none" and left_meaning not in {"", "none", "unknown"}:
        return MatchResult("no-match", 0.0, "The candidate explicitly has no matching semantic meaning.")
    if not left_meaning or left_meaning in {"unknown", "none"} or not right_meaning or right_meaning == "unknown":
        return MatchResult("unknown", 0.0, "No supported semantic meaning is available.")
    meaning_score = 1.0 if left_meaning == right_meaning else _overlap(left_meaning, right_meaning)
    if meaning_score < 0.6:
        return MatchResult("no-match", round(meaning_score * 0.64, 3),
                           "The extracted meaning differs; subject or sender cannot override it.")

    score = 0.64 + 0.12 * meaning_score
    signals = ["meaning"]
    if left.subtopic and right.subtopic:
        similarity = 1.0 if _clean(left.subtopic) == _clean(right.subtopic) else _overlap(left.subtopic, right.subtopic)
        score += 0.12 * similarity
        if similarity >= 0.6:
            signals.append("subtopic")
    subject_score = _overlap(left.subject, right.subject)
    score += 0.08 * subject_score
    if subject_score >= 0.25:
        signals.append("subject")
    # Sender is intentionally a small tie-breaker.  It is never authentication
    # evidence and this bonus is unreachable when semantic meaning mismatches.
    if left.sender and right.sender and left.sender.strip().casefold() == right.sender.strip().casefold():
        score += 0.04
        signals.append("sender-context-only")
    score = min(score, 1.0)
    if score < threshold:
        return MatchResult("no-match", round(score, 3), "The semantic contexts are not similar enough.", tuple(signals))
    return MatchResult("match", round(score, 3),
                       "The extracted meaning matches; topic, subject, and sender are supplemental context only.",
                       tuple(signals))


def skills_match(saved_config: Mapping[str, Any], email: Any, proposal: Any) -> MatchResult:
    """Optional adapter for incrementally moving existing Skills to this matcher."""
    saved = SemanticContext(saved_config.get("kind", ""), saved_config.get("subtopic", ""),
                            saved_config.get("subject", ""), saved_config.get("sender", ""),
                            saved_config.get("evidence", ""))
    return compare(saved, from_email_proposal(email, proposal))
