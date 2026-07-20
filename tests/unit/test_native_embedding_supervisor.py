import sys
from io import StringIO

import pytest

from code_harness.domain.errors import EmbeddingUnavailableError
from code_harness.domain.models.semantic import EmbeddingIdentity
from code_harness.infrastructure.embeddings import NativeEmbeddingSupervisor, native_worker

_SUCCESS_WORKER = """
import json, sys
p = json.loads(sys.stdin.read())
if p['operation'] == 'identity':
    r = {'identity': {'provider': 'fake', 'provider_version': '1', 'model_id': 'stub',
         'dimensions': 2, 'strategy': 'test'}}
elif p['operation'] == 'embed_documents':
    r = {'vectors': [[1.0, 0.0] for _ in p['texts']]}
else:
    r = {'vector': [0.0, 1.0]}
sys.stdout.write(json.dumps(r))
"""


def test_embedding_supervisor_decodes_identity_and_vectors(tmp_path) -> None:
    supervisor = NativeEmbeddingSupervisor(
        "stub",
        cache_dir=tmp_path / "models",
        command=(sys.executable, "-c", _SUCCESS_WORKER),
    )

    assert supervisor.identity.model_id == "stub"
    assert supervisor.embed_documents(("one", "two")) == ((1.0, 0.0), (1.0, 0.0))
    assert supervisor.embed_query("query") == (0.0, 1.0)
    supervisor.shutdown()
    supervisor.shutdown()


def test_embedding_supervisor_round_trips_unicode_independently_of_console_encoding(
    tmp_path,
) -> None:
    expected = "dicionário semântico — ação e validação"
    worker = f"""
import json, sys
p = json.loads(sys.stdin.read())
ok = p.get('texts') == [{expected!r}]
sys.stdout.write(json.dumps({{'vectors': [[1.0, 0.0]] if ok else [[0.0, 1.0]]}}))
"""
    supervisor = NativeEmbeddingSupervisor(
        "stub",
        cache_dir=tmp_path / "models",
        command=(sys.executable, "-c", worker),
    )

    assert supervisor.embed_documents((expected,)) == ((1.0, 0.0),)


def test_embedding_supervisor_contains_crashes(tmp_path) -> None:
    supervisor = NativeEmbeddingSupervisor(
        "stub",
        cache_dir=tmp_path / "models",
        command=(sys.executable, "-c", "import sys; sys.stderr.write('native crash'); sys.exit(7)"),
    )

    with pytest.raises(EmbeddingUnavailableError, match="native crash"):
        _ = supervisor.identity


def test_embedding_supervisor_times_out(tmp_path) -> None:
    supervisor = NativeEmbeddingSupervisor(
        "stub",
        cache_dir=tmp_path / "models",
        timeout_seconds=0.05,
        command=(sys.executable, "-c", "import time; time.sleep(5)"),
    )

    with pytest.raises(EmbeddingUnavailableError, match="timed out"):
        supervisor.embed_query("query")


class _WorkerProvider:
    identity = EmbeddingIdentity("fake", "1", "stub", 2, "test")

    def embed_documents(self, texts):
        return tuple((1.0, float(index)) for index, _ in enumerate(texts))

    def embed_query(self, text):
        return (0.0, float(bool(text)))


def test_native_worker_dispatches_and_serializes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native_worker, "_provider", lambda payload: _WorkerProvider())
    base = {"model": "stub"}

    assert (
        native_worker._execute({**base, "operation": "identity"})["identity"]["model_id"] == "stub"
    )
    assert native_worker._execute({**base, "operation": "embed_documents", "texts": ["a", "b"]})[
        "vectors"
    ] == ((1.0, 0.0), (1.0, 1.0))
    assert native_worker._execute({**base, "operation": "embed_query", "text": "a"})["vector"] == (
        0.0,
        1.0,
    )
    with pytest.raises(ValueError, match="Unsupported"):
        native_worker._execute({**base, "operation": "unknown"})


def test_native_worker_returns_actionable_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    certificate_error = RuntimeError("CERTIFICATE_VERIFY_FAILED")
    assert "CODE_HARNESS_CA_BUNDLE" in native_worker._friendly_error(certificate_error)
    assert native_worker._friendly_error(RuntimeError("plain failure")) == "plain failure"

    stdin = StringIO("not json")
    stdout = StringIO()
    monkeypatch.setattr(native_worker.sys, "stdin", stdin)
    monkeypatch.setattr(native_worker.sys, "stdout", stdout)
    native_worker.main()

    assert "JSONDecodeError" in stdout.getvalue()
