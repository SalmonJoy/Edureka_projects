"""Message state and MongoDB transcript storage, separate from graph checkpoints."""

from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from langchain_core.messages import AIMessage, HumanMessage, messages_from_dict, messages_to_dict
from langgraph.graph import MessagesState
from pymongo import ASCENDING, AsyncMongoClient
from pymongo.errors import CollectionInvalid, OperationFailure, PyMongoError

from langGraph_PoC.config import ENV_FILE


DATABASE = "Edureka"
COLLECTION = "conversation_messages"


class ConversationState(MessagesState, total=False):
    conversation_id: str


class ConversationStorageError(RuntimeError):
    """A sanitized storage error that is safe to display in the terminal."""


def conversation_context(messages: list, limit: int = 20) -> list[dict]:
    """Give extraction recent conversational text, without message metadata."""
    return [
        {"role": "user" if message.type == "human" else "assistant", "content": message.content}
        for message in messages[-limit:]
        if message.type in ("human", "ai")
    ]


def review_text(payload: dict) -> str:
    """A user-visible review summary, without persisting internal API payloads."""
    employee, leave = payload["employee"], payload["leave"]
    return (
        f"Employee: {employee['name']} ({employee['employee_id']})\n"
        f"Leave: {leave['start_date']} to {leave['end_date']} "
        f"({payload['requested_days']} working days, {leave['leave_type']})\n"
        f"Reason: {leave['reason']}\nAvailable balance: {payload['leave_balance']} days\n"
        "Eligibility: " + " ".join(payload["eligibility"]["reasons"])
        + "\nSubmit this leave application? [yes/no]"
    )


class ConversationMemory:
    """One active writer per conversation; MongoDB stores one document per message."""

    def __init__(self, client, collection, conversation_id: str):
        self.client = client
        self.collection = collection
        self.conversation_id = conversation_id
        self._next_sequence = 0

    @classmethod
    async def connect(cls, conversation_id: str | None = None, *, env_file: Path = ENV_FILE):
        values = dotenv_values(env_file) if env_file.is_file() else {}
        uri = (values.get("MONGODB_URI") or os.getenv("MONGODB_URI", "")).strip()
        if not uri:
            raise ConversationStorageError(f"Missing MONGODB_URI. Set it in {env_file}.")
        if conversation_id is not None and not conversation_id.strip():
            raise ConversationStorageError("Conversation ID must not be blank.")
        client = None
        try:
            client = AsyncMongoClient(
                uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000,
                socketTimeoutMS=10000, tz_aware=True,
            )
            await client.admin.command("ping")
            database = client[DATABASE]
            try:
                await database.create_collection(COLLECTION)
            except CollectionInvalid:
                pass
            except OperationFailure as error:
                if error.code != 48:  # Another client may have just created the collection.
                    raise
            collection = database[COLLECTION]
            await collection.create_index(
                [("conversation_id", ASCENDING), ("sequence", ASCENDING)],
                unique=True, name="conversation_sequence",
            )
            memory = cls(client, collection, conversation_id or str(uuid4()))
            await memory.load()
            return memory
        except (PyMongoError, ValueError, ConversationStorageError):
            if client is not None:
                await client.close()
            raise ConversationStorageError(
                "Cannot initialize Edureka.conversation_messages. Check the MongoDB "
                "connection, credentials, network access, and database permissions."
            ) from None

    async def close(self):
        await self.client.close()

    async def load(self) -> list:
        try:
            cursor = self.collection.find({"conversation_id": self.conversation_id}).sort("sequence", ASCENDING)
            documents = await cursor.to_list(length=None)
            messages = messages_from_dict([document["message"] for document in documents])
            if any(message.type not in ("human", "ai") for message in messages):
                raise ValueError("Unsupported transcript message")
            self._next_sequence = documents[-1]["sequence"] + 1 if documents else 0
            return messages
        except (PyMongoError, ValueError, TypeError, KeyError):
            raise ConversationStorageError(
                "Cannot load this conversation from MongoDB. Check the connection and stored transcript."
            ) from None

    async def save(self, message, thread_id: str):
        """Upsert a sanitized message; replay preserves its original sequence/timestamp."""
        if message.type not in ("human", "ai") or not isinstance(message.content, str) or not message.id:
            raise ConversationStorageError("Only identified user/assistant text messages can be saved.")
        message_class = HumanMessage if message.type == "human" else AIMessage
        clean_message = message_class(content=message.content, id=message.id)
        try:
            result = await self.collection.update_one(
                {"_id": f"{self.conversation_id}:{message.id}"},
                {
                    "$set": {"message": messages_to_dict([clean_message])[0]},
                    "$setOnInsert": {
                        "conversation_id": self.conversation_id, "thread_id": thread_id,
                        "message_id": message.id, "sequence": self._next_sequence,
                        "created_at": datetime.now(timezone.utc),
                    },
                },
                upsert=True,
            )
            if result.upserted_id is not None:
                self._next_sequence += 1
        except PyMongoError:
            raise ConversationStorageError(
                "Conversation history could not be saved to MongoDB. Check the connection; "
                "no workflow retry was made."
            ) from None

    async def start_request(self, request: str, thread_id: str) -> dict:
        history = await self.load()
        message = HumanMessage(content=request, id=f"{thread_id}:request")
        await self.save(message, thread_id)
        return {"conversation_id": self.conversation_id, "messages": [*history, message]}

    async def record(self, graph, config: dict, role: str, content: str, event_id: str):
        """Persist before resuming; add_messages merges repeated IDs in graph state."""
        thread_id = config["configurable"]["thread_id"]
        message_class = HumanMessage if role == "user" else AIMessage
        message = message_class(content=content, id=f"{thread_id}:{event_id}")
        await self.save(message, thread_id)
        await graph.aupdate_state(config, {"messages": [message]})
