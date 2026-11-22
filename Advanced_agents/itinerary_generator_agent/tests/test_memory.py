from unittest.mock import Mock

import httpx
import pytest
from qdrant_client import QdrantClient, models

from agent.errors import ServiceError
from agent.memory import APP_ID, QdrantMemory
from agent.models import TripRequest
from conftest import finish

pytestmark = pytest.mark.filterwarnings("ignore:Payload indexes have no effect in the local Qdrant:UserWarning")


def embedding_http(seen):
    def handler(request):
        import json

        payload = json.loads(request.content)
        seen.append(payload)
        # A deterministic test embedding, not a replacement for the real Nomic model.
        return httpx.Response(200, json={"embeddings": [[1.0, *([0.0] * 767)] for _ in payload["input"]]})
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def memory(settings):
    client = QdrantClient(":memory:")
    client.create_collection(settings.collection, vectors_config={"": models.VectorParams(size=768, distance=models.Distance.COSINE)})
    seen = []
    http = embedding_http(seen)
    memory = QdrantMemory(settings, client=client, http=http)
    yield memory, client, seen
    http.close()
    client.close()


def test_idempotent_writes_and_filtered_retrieval(memory, runtime):
    memory, client, seen = memory
    state = finish(runtime, runtime.start_trip(TripRequest(user_id="alice", preferences="Quiet cultural trips")))
    assert memory.save(state) == 2
    assert memory.save(state) == 2
    assert client.count("Edureka", exact=True).count == 2
    found = memory.retrieve(state["request"])
    assert {record["type"] for record in found} == {"preference", "experience"}
    assert memory.vector_name == ""
    assert seen[0]["input"][0].startswith("search_document: ")
    assert seen[-1]["input"][0].startswith("search_query: ")
    assert memory.retrieve(TripRequest(user_id="bob").model_dump()) == []
    assert memory.retrieve(state["request"], is_test=True) == []

    # Foreign application points never enter agent memory retrieval.
    client.upsert("Edureka", points=[models.PointStruct(
        id=1, vector={"": [1.0, *([0.0] * 767)]},
        payload={"app_id": "another-app", "user_id": "alice", "is_test": False,
                 "embedding_model": memory.settings.embedding_model, "text": "Foreign data"},
    )])
    assert len(memory.retrieve(state["request"])) == 2
    # Test memory is isolated even for the same user ID.
    state["run_id"] = "test-run"
    state["is_test"] = True
    memory.save(state)
    assert len(memory.retrieve(state["request"])) == 2
    assert len(memory.retrieve(state["request"], is_test=True)) == 2


def test_existing_collection_is_never_recreated(settings):
    client = QdrantClient(":memory:")
    client.create_collection("Edureka", vectors_config={"": models.VectorParams(size=384, distance=models.Distance.COSINE)})
    memory = QdrantMemory(settings, client=client, http=Mock())
    with pytest.raises(ServiceError, match="768-dimensional"):
        memory.prepare()
    assert client.get_collection("Edureka").config.params.vectors[""].size == 384
    client.close()


def test_unexpected_vector_name_is_not_silently_changed(settings):
    client = QdrantClient(":memory:")
    client.create_collection("Edureka", vectors_config={"other": models.VectorParams(size=768, distance=models.Distance.COSINE)})
    memory = QdrantMemory(settings, client=client, http=Mock())
    with pytest.raises(ServiceError, match="empty-string vector name"):
        memory.prepare()
    client.close()


def test_bad_embedding_shape_is_rejected(memory):
    memory, _, _ = memory
    memory.http.close()
    memory.http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"embeddings": [[0.0] * 384]})))
    with pytest.raises(ServiceError, match="Ollama embedding failed"):
        memory.retrieve(TripRequest().model_dump())
    memory.http.close()


def test_qdrant_setup_failure_is_readable(settings):
    client = Mock()
    client.get_collection.side_effect = RuntimeError("raw secret")
    with pytest.raises(ServiceError) as caught:
        QdrantMemory(settings, client=client, http=Mock()).prepare()
    assert "raw secret" not in str(caught.value)
    assert not client.create_collection.called
