"""Persistent semantic memory: local Ollama embeddings + existing Qdrant data."""

import json
import math
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

import httpx
from qdrant_client import QdrantClient, models

from agent.config import Settings
from agent.errors import ServiceError

APP_ID = "itinerary-generator-agent"
VECTOR_SIZE = 768


class QdrantMemory:
    def __init__(self, settings: Settings, client=None, http=None):
        self.settings = settings
        self.client = client if client is not None else QdrantClient(
            url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=30,
        )
        self.http = http if http is not None else httpx.Client(timeout=120, trust_env=False)
        self._owns_client = client is None
        self._owns_http = http is None
        self._ready = False
        self.vector_name: str | None = None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()
        if self._owns_http:
            self.http.close()

    def prepare(self) -> None:
        if self._ready:
            return
        try:
            collection = self.client.get_collection(self.settings.collection)
            vectors = collection.config.params.vectors
            if isinstance(vectors, dict):
                if "" not in vectors:
                    raise ServiceError("Qdrant collection must contain its expected empty-string vector name. No collection was recreated.")
                self.vector_name = ""
                vector = vectors[""]
            else:
                self.vector_name = None
                vector = vectors
            if vector is None or vector.size != VECTOR_SIZE or vector.distance != models.Distance.COSINE:
                raise ServiceError("Qdrant memory needs 768-dimensional cosine vectors. Existing data was left unchanged.")
            for name, schema in [
                ("app_id", models.PayloadSchemaType.KEYWORD),
                ("user_id", models.PayloadSchemaType.KEYWORD),
                ("embedding_model", models.PayloadSchemaType.KEYWORD),
                ("is_test", models.PayloadSchemaType.BOOL),
            ]:
                if name not in collection.payload_schema:
                    self.client.create_payload_index(self.settings.collection, name, field_schema=schema, wait=True)
            self._ready = True
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("Qdrant memory setup failed. Check credentials, collection permissions, and the existing collection name.") from None

    def _embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        prefix = "search_query: " if query else "search_document: "
        try:
            response = self.http.post(
                self.settings.ollama_url.rstrip("/") + "/api/embed",
                json={"model": self.settings.embedding_model, "input": [prefix + text for text in texts]},
            )
            response.raise_for_status()
            vectors = response.json()["embeddings"]
            if len(vectors) != len(texts) or any(
                len(vector) != VECTOR_SIZE or not all(math.isfinite(value) for value in vector)
                for vector in vectors
            ):
                raise ValueError("Wrong embedding shape")
            return vectors
        except Exception:
            raise ServiceError("Ollama embedding failed. Start local Ollama and check nomic-embed-text (768 dimensions). Cold loads may take a minute.") from None

    def retrieve(self, request: dict, *, is_test: bool = False) -> list[dict]:
        self.prepare()
        query = f"{request['destination_id']} trip, {request['nights']} nights. Preferences: {request['preferences']}"
        vector = self._embed([query], query=True)[0]
        try:
            result = self.client.query_points(
                collection_name=self.settings.collection, query=vector,
                using=self.vector_name, limit=5, with_payload=True,
                query_filter=models.Filter(must=[
                    models.FieldCondition(key="app_id", match=models.MatchValue(value=APP_ID)),
                    models.FieldCondition(key="user_id", match=models.MatchValue(value=request["user_id"])),
                    models.FieldCondition(key="embedding_model", match=models.MatchValue(value=self.settings.embedding_model)),
                    models.FieldCondition(key="is_test", match=models.MatchValue(value=is_test)),
                ]),
            )
            return [
                {"id": str(point.id), "score": point.score, "type": point.payload["memory_type"],
                 "text": point.payload["text"], "created_at": point.payload["created_at"]}
                for point in result.points
            ]
        except Exception:
            raise ServiceError("Qdrant semantic retrieval failed. No substitute memories were generated.") from None

    def save(self, state: dict) -> int:
        self.prepare()
        request = state["request"]
        records = []
        if request["preferences"].strip():
            records.append(("preference", request["preferences"].strip()))
        # Store observed facts, not an LLM's invented account of what happened.
        experience = {
            "destination": request["destination_id"], "status": state["status"],
            "observations": state["observations"], "revisions": state["replan_count"],
            "plan": state.get("plan"), "budget": state.get("budget"),
            "reflection": state.get("reflection"), "reason": state.get("error"),
        }
        records.append(("experience", json.dumps(experience, ensure_ascii=False)))
        vectors = self._embed([text for _, text in records])
        timestamp = datetime.now(timezone.utc).isoformat()
        points = []
        for (kind, text), vector in zip(records, vectors):
            point_id = str(uuid5(NAMESPACE_URL, f"{APP_ID}/{request['user_id']}/{state['run_id']}/{kind}"))
            payload = {
                "app_id": APP_ID, "user_id": request["user_id"], "run_id": state["run_id"],
                "memory_type": kind, "created_at": timestamp,
                "embedding_model": self.settings.embedding_model,
                "is_test": state.get("is_test", False), "text": text,
            }
            # A dict preserves Edureka's actual empty-string vector name.
            stored_vector = vector if self.vector_name is None else {self.vector_name: vector}
            points.append(models.PointStruct(id=point_id, vector=stored_vector, payload=payload))
        try:
            self.client.upsert(self.settings.collection, points=points, wait=True)
        except Exception:
            raise ServiceError("Qdrant memory write was not confirmed. The itinerary result is retained; retrying the same run uses stable record IDs.") from None
        return len(points)
