import re

from code_harness.domain.enums import QueryKind
from code_harness.domain.models.hybrid import QueryClassification

_QUOTED = re.compile(r'"([^"\r\n]+)"|\'([^\'\r\n]+)\'')
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*(?:[./:#\\-][A-Za-z0-9_.$-]+)*")
_EXTENSION = re.compile(r"(?:^|\s)([^\s]+\.[A-Za-z0-9]{1,10})(?:$|\s)")
_QUESTION_PREFIXES = (
    "como ",
    "onde ",
    "qual ",
    "quais ",
    "por que ",
    "porque ",
    "what ",
    "where ",
    "how ",
    "which ",
    "why ",
)
_ERROR_TERMS = (
    "error",
    "erro",
    "exception",
    "falha",
    "failed",
    "failure",
    "refused",
    "timeout",
    "traceback",
)


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _is_strong_identifier(value: str) -> bool:
    name = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return (
        "_" in name
        or "." in value
        or "/" in value
        or "\\" in value
        or "::" in value
        or any(character.isupper() for character in name[1:])
        or (len(name) > 1 and name.isupper())
        or any(character.isdigit() for character in name)
    )


class QueryClassifier:
    def classify(self, query: str) -> QueryClassification:
        stripped = query.strip()
        folded = stripped.casefold()
        signals: list[str] = []
        quoted = [left or right for left, right in _QUOTED.findall(stripped)]
        if quoted:
            signals.append("quoted_text")

        raw_tokens = _TOKEN.findall(stripped)
        identifiers = [value for value in raw_tokens if _is_strong_identifier(value)]
        path_terms = [
            value
            for value in (*raw_tokens, *_EXTENSION.findall(stripped))
            if "/" in value or "\\" in value or "." in value
        ]
        if identifiers:
            signals.append("strong_identifier")
        if path_terms:
            signals.append("path_or_extension")
        if any(character in stripped for character in ("_", "::")):
            signals.append("identifier_punctuation")
        error_message = any(term in folded for term in _ERROR_TERMS)
        if error_message:
            signals.append("error_message")

        word_count = len(stripped.split())
        conceptual = folded.startswith(_QUESTION_PREFIXES) or word_count >= 6
        if folded.startswith(_QUESTION_PREFIXES):
            signals.append("question_prefix")
        elif word_count >= 6:
            signals.append("natural_language_length")

        exact = bool(quoted or identifiers or path_terms or error_message)
        if exact and conceptual:
            kind = QueryKind.MIXED
        elif exact:
            kind = QueryKind.EXACT
        else:
            kind = QueryKind.CONCEPTUAL
            if not signals:
                signals.append("no_strong_identifier")

        lexical_terms = _unique([*quoted, *identifiers])
        if not lexical_terms:
            lexical_terms = (stripped,)
        return QueryClassification(
            kind=kind,
            signals=_unique(signals),
            identifiers=_unique(identifiers)[:5],
            lexical_terms=lexical_terms[:5],
            path_terms=_unique(path_terms)[:3],
        )
