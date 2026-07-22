from code_harness.application.ranking.lexical_expansion import expand_lexical_identifiers
from code_harness.application.ranking.query_classifier import QueryClassifier
from code_harness.domain.enums import QueryKind


def test_expand_lexical_identifiers_builds_camel_and_snake_variants() -> None:
    expansion = expand_lexical_identifiers("criar envelope para assinatura digital")

    values = {item.value for item in expansion.generated_identifiers}
    assert "criarEnvelope" in values
    assert "CRIAR_ENVELOPE" in values or "criar_envelope" in values
    assert expansion.input_terms


def test_classifier_expands_conceptual_queries() -> None:
    classification = QueryClassifier().classify("criar envelope para assinatura digital")

    assert classification.kind is QueryKind.CONCEPTUAL
    assert "lexical_expansion" in classification.signals
    joined = " ".join(classification.identifiers + classification.lexical_terms)
    assert "criarEnvelope" in joined or "criar_envelope" in joined
