"""Deterministic identifier expansion for conceptual hybrid search."""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "for",
        "from",
        "how",
        "in",
        "into",
        "of",
        "on",
        "or",
        "para",
        "por",
        "the",
        "to",
        "uma",
        "um",
        "with",
        "what",
        "where",
        "which",
        "why",
        "como",
        "onde",
        "qual",
        "quais",
    }
)


@dataclass(frozen=True, slots=True)
class ExpandedTerm:
    value: str
    weight: float
    origin: str


@dataclass(frozen=True, slots=True)
class LexicalExpansion:
    input_terms: tuple[str, ...]
    generated_identifiers: tuple[ExpandedTerm, ...]
    discarded_count: int
    limit_reached: bool


def expand_lexical_identifiers(
    query: str,
    *,
    max_expanded_terms: int = 24,
    max_ngrams: int = 2,
    max_identifier_candidates: int = 12,
    min_token_length: int = 3,
) -> LexicalExpansion:
    raw_tokens = [token for token in _TOKEN.findall(query) if token]
    terms = [
        token
        for token in raw_tokens
        if len(token) >= min_token_length and token.casefold() not in _STOPWORDS
    ]
    generated: list[ExpandedTerm] = []
    seen: set[str] = set()

    def add(value: str, weight: float, origin: str) -> None:
        if not value or value in seen:
            return
        seen.add(value)
        generated.append(ExpandedTerm(value, weight, origin))

    folded_query = query.strip()
    if folded_query:
        add(folded_query, 1.0, "original")

    for term in terms:
        add(term, 1.0, "original_token")
        add(_to_snake(term), 0.85, "snake")
        add(_to_screaming_snake(term), 0.85, "screaming_snake")
        add(_to_camel(term), 0.85, "camel")
        add(_to_pascal(term), 0.85, "pascal")

    for size in range(2, max_ngrams + 1):
        for index in range(0, max(0, len(terms) - size + 1)):
            chunk = terms[index : index + size]
            add(_join_camel(chunk), 0.70, "bigram_camel")
            add(_join_pascal(chunk), 0.70, "bigram_pascal")
            add("_".join(part.casefold() for part in chunk), 0.70, "bigram_snake")
            add("_".join(part.upper() for part in chunk), 0.70, "bigram_screaming")

    for term in terms:
        add(term.casefold(), 0.35, "token")

    limited = generated[:max_expanded_terms]
    identifiers = tuple(
        item for item in limited if _looks_like_identifier(item.value)
    )[:max_identifier_candidates]
    discarded = max(0, len(generated) - len(limited)) + max(
        0, len(limited) - len(identifiers)
    )
    return LexicalExpansion(
        input_terms=tuple(terms),
        generated_identifiers=identifiers,
        discarded_count=discarded,
        limit_reached=len(generated) > max_expanded_terms
        or len([item for item in limited if _looks_like_identifier(item.value)])
        > max_identifier_candidates,
    )


def _looks_like_identifier(value: str) -> bool:
    return bool(_TOKEN.fullmatch(value)) and (
        "_" in value or any(char.isupper() for char in value[1:]) or value.isupper()
    )


def _to_snake(value: str) -> str:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return spaced.replace("-", "_").casefold()


def _to_screaming_snake(value: str) -> str:
    return _to_snake(value).upper()


def _to_camel(value: str) -> str:
    parts = [part for part in re.split(r"[_\-\s]+", value) if part]
    if not parts:
        return value
    head, *tail = parts
    return head.casefold() + "".join(part[:1].upper() + part[1:].casefold() for part in tail)


def _to_pascal(value: str) -> str:
    camel = _to_camel(value)
    return camel[:1].upper() + camel[1:] if camel else camel


def _join_camel(parts: list[str]) -> str:
    if not parts:
        return ""
    head, *tail = parts
    return head.casefold() + "".join(part[:1].upper() + part[1:].casefold() for part in tail)


def _join_pascal(parts: list[str]) -> str:
    return "".join(part[:1].upper() + part[1:].casefold() for part in parts)
