"""MongoDB is mocked here; live database checks use an isolated conversation."""

import copy
from contextlib import redirect_stdout
from datetime import datetime
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from pymongo.errors import CollectionInvalid, ConnectionFailure

from langGraph_PoC import conversation_memory as module
from langGraph_PoC import main as cli
from langGraph_PoC.conversation_memory import (
    ConversationMemory, ConversationStorageError, conversation_context,
)
from langGraph_PoC.workflow import build_graph, initial_state
from langGraph_PoC.tests.test_workflow import FakeClients, TODAY, extracted


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, key, direction):
        self.documents.sort(key=lambda document: document[key], reverse=direction < 0)
        return self

    async def to_list(self, length=None):
        return copy.deepcopy(self.documents)


class FakeCollection:
    def __init__(self):
        self.documents = {}
        self.create_index = AsyncMock()
        self.fail_read = False
        self.fail_message = lambda message: False

    def find(self, query):
        if self.fail_read:
            raise ConnectionFailure("sensitive connection information")
        return FakeCursor([
            copy.deepcopy(document) for document in self.documents.values()
            if document["conversation_id"] == query["conversation_id"]
        ])

    async def update_one(self, query, update, upsert=False):
        message = update["$set"]["message"]
        if self.fail_message(message):
            raise ConnectionFailure("sensitive connection information")
        key = query["_id"]
        inserted = key not in self.documents
        if inserted:
            document = {"_id": key, **copy.deepcopy(update["$setOnInsert"])}
            if any(
                other["conversation_id"] == document["conversation_id"]
                and other["sequence"] == document["sequence"]
                for other in self.documents.values()
            ):
                raise AssertionError("Duplicate sequence")
            self.documents[key] = document
        self.documents[key].update(copy.deepcopy(update["$set"]))
        return SimpleNamespace(upserted_id=key if inserted else None)


def memory(collection=None, conversation_id="test-conversation"):
    client = SimpleNamespace(close=AsyncMock())
    return ConversationMemory(client, collection or FakeCollection(), conversation_id)


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_roundtrip_order_upsert_metadata_and_reload(self):
        store = memory()
        user = HumanMessage(content="Leave please\nFamily visit", id="user-1")
        assistant = AIMessage(content="Which dates?", id="assistant-1", additional_kwargs={"reasoning_content": "private reasoning"})
        await store.save(user, "thread-1")
        await store.save(assistant, "thread-1")
        await store.save(assistant, "thread-1")
        restored = await store.load()
        self.assertEqual([message.id for message in restored], ["user-1", "assistant-1"])
        self.assertEqual(restored[0].content, user.content)
        self.assertEqual(restored[1].additional_kwargs, {})
        self.assertEqual(len(store.collection.documents), 2)
        records = list(store.collection.documents.values())
        self.assertEqual([record["sequence"] for record in records], [0, 1])
        self.assertTrue(all(record["thread_id"] == "thread-1" for record in records))
        self.assertTrue(all(isinstance(record["created_at"], datetime) and record["created_at"].utcoffset() is not None for record in records))
        reopened = memory(store.collection)
        self.assertEqual([message.content for message in await reopened.load()], [user.content, assistant.content])
        await reopened.save(HumanMessage(content="November 16", id="user-2"), "thread-2")
        self.assertEqual(list(store.collection.documents.values())[-1]["sequence"], 2)

    async def test_conversations_are_isolated_and_context_is_bounded(self):
        collection = FakeCollection()
        first, second = memory(collection, "first"), memory(collection, "second")
        for i in range(25):
            await first.save(HumanMessage(content=f"Message {i}", id=f"id-{i}"), "thread")
        await second.save(HumanMessage(content="Other conversation", id="id-0"), "thread")
        self.assertEqual(len(await second.load()), 1)
        history = await first.load()
        recent = conversation_context(history)
        self.assertEqual(len(history), 25)
        self.assertEqual(len(recent), 20)
        self.assertEqual(recent[0], {"role": "user", "content": "Message 5"})
        self.assertTrue(all(set(message) == {"role", "content"} for message in recent))

    async def test_initialize_creates_or_reuses_collection_and_closes(self):
        for exists in (False, True):
            collection = FakeCollection()
            database = MagicMock()
            database.__getitem__.return_value = collection
            database.create_collection = AsyncMock(side_effect=CollectionInvalid("exists") if exists else None)
            client = MagicMock()
            client.__getitem__.return_value = database
            client.admin.command = AsyncMock()
            client.close = AsyncMock()
            with TemporaryDirectory() as folder:
                path = Path(folder) / ".env"
                path.write_text("MONGODB_URI=mongodb://test-user:test-password@localhost/\n", encoding="utf-8")
                with patch.object(module, "AsyncMongoClient", return_value=client) as constructor:
                    with patch.dict(os.environ, {"MONGODB_URI": "mongodb://inherited/"}):
                        store = await ConversationMemory.connect("saved-conversation", env_file=path)
                self.assertEqual(constructor.call_args.args[0], "mongodb://test-user:test-password@localhost/")
            client.__getitem__.assert_called_once_with("Edureka")
            database.create_collection.assert_awaited_once_with("conversation_messages")
            collection.create_index.assert_awaited_once()
            self.assertTrue(collection.create_index.call_args.kwargs["unique"])
            self.assertEqual(store.conversation_id, "saved-conversation")
            await store.close()
            client.close.assert_awaited_once()

    async def test_missing_configuration_and_database_failures_are_sanitized(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ConversationStorageError, "Missing MONGODB_URI"):
                await ConversationMemory.connect(env_file=Path("nonexistent-test.env"))
        store = memory()
        store.collection.fail_read = True
        with self.assertRaises(ConversationStorageError) as error:
            await store.load()
        self.assertNotIn("sensitive", str(error.exception))
        store.collection.fail_read = False
        store.collection.fail_message = lambda message: True
        with self.assertRaises(ConversationStorageError) as error:
            await store.save(HumanMessage(content="test", id="id"), "thread")
        self.assertNotIn("sensitive", str(error.exception))


class ConversationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def run_cli(self, graph, store, request, answers):
        with patch.object(cli, "initial_state", side_effect=lambda text: initial_state(text, today=TODAY)):
            with patch("builtins.input", side_effect=answers), redirect_stdout(io.StringIO()) as output:
                result = await cli.run_request(graph, request, memory=store)
        return result, output.getvalue()

    async def test_duplicate_message_ids_merge_in_state_and_database(self):
        clients, store = FakeClients(), memory()
        graph = build_graph(clients)
        config = {"configurable": {"thread_id": "reducer-test"}}
        state = initial_state("Vacation", today=TODAY)
        state.update(await store.start_request("Vacation", "reducer-test"))
        await graph.ainvoke(state, config)
        await store.record(graph, config, "assistant", "Review this request", "review")
        await store.record(graph, config, "assistant", "Review this request", "review")
        snapshot = await graph.aget_state(config)
        self.assertEqual(len(snapshot.values["messages"]), 2)
        self.assertEqual(len(await store.load()), 2)
        result = await graph.ainvoke(Command(resume=False), config)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(clients.submissions, [])

    async def test_confirmation_persists_messages_and_graph_state_without_replay(self):
        clients, store = FakeClients(), memory()
        graph = build_graph(clients)
        result, _ = await self.run_cli(graph, store, "Vacation", ["yes"])
        messages = await store.load()
        self.assertEqual([message.type for message in messages], ["human", "ai", "human", "ai"])
        self.assertEqual(messages[2].content, "yes")
        self.assertIn("Submit this leave application?", messages[1].content)
        self.assertNotIn("arjun.rao@example.com", messages[1].content)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["conversation_id"], store.conversation_id)
        self.assertEqual([message.id for message in result["messages"]], [message.id for message in messages])
        self.assertEqual(len(clients.submissions), 1)
        self.assertEqual(len(clients.assessments), 1)

    async def test_clarification_resume_and_cancellation_are_recorded(self):
        clients, store = FakeClients(), memory()
        clients.extractions = [extracted(reason=None), extracted()]
        final, _ = await self.run_cli(build_graph(clients), store, "Leave November 10-12", ["Family vacation", "no"])
        messages = await store.load()
        self.assertEqual(len(messages), 6)
        self.assertEqual(messages[2].content, "Family vacation")
        self.assertEqual(messages[4].content, "no")
        self.assertIn("cancelled", messages[-1].content.lower())
        self.assertEqual(final["status"], "cancelled")
        self.assertEqual(len(clients.extraction_contexts), 2)
        self.assertIn("Family vacation", str(clients.extraction_contexts[1]["conversation_history"]))
        self.assertEqual(clients.submissions, [])

    async def test_cancel_during_clarification_is_saved(self):
        clients, store = FakeClients(), memory()
        clients.extractions = [extracted(reason=None)]
        await self.run_cli(build_graph(clients), store, "Leave", ["/cancel"])
        messages = await store.load()
        self.assertEqual(messages[-2].content, "/cancel")
        self.assertEqual(messages[-1].content, "Request cancelled.")
        self.assertEqual(clients.submissions, [])

    async def test_next_request_uses_history_and_fresh_confirmation(self):
        clients, store = FakeClients(), memory()
        clients.extractions = [extracted(), extracted(reason="Same family trip")]
        graph = build_graph(clients)
        first, _ = await self.run_cli(graph, store, "Vacation", ["yes"])
        second, _ = await self.run_cli(graph, store, "Same dates and reason again", ["no"])
        history = clients.extraction_contexts[1]["conversation_history"]
        self.assertIn(first["application"]["application_id"], str(history))
        self.assertEqual(history[-1]["content"], "Same dates and reason again")
        self.assertEqual(second["status"], "cancelled")
        self.assertNotIn("application", second)
        self.assertFalse(second["confirmed"])
        self.assertEqual(len(clients.submissions), 1)
        self.assertEqual(len({record["thread_id"] for record in store.collection.documents.values()}), 2)
        self.assertEqual(len(await store.load()), 8)

    async def test_confirmation_storage_failure_stops_before_post(self):
        clients, store = FakeClients(), memory()
        store.collection.fail_message = lambda message: message["type"] == "human" and message["data"]["content"] == "yes"
        with self.assertRaises(ConversationStorageError):
            await self.run_cli(build_graph(clients), store, "Vacation", ["yes"])
        self.assertEqual(clients.submissions, [])

    async def test_final_storage_failure_displays_receipt_without_repeating_post(self):
        clients, store = FakeClients(), memory()
        store.collection.fail_message = lambda message: "submitted for" in message["data"]["content"]
        output = io.StringIO()
        with patch.object(cli, "initial_state", side_effect=lambda text: initial_state(text, today=TODAY)):
            with patch("builtins.input", return_value="yes"), redirect_stdout(output):
                with self.assertRaises(ConversationStorageError):
                    await cli.run_request(build_graph(clients), "Vacation", memory=store)
        self.assertIn("submitted for", output.getvalue())
        self.assertEqual(len(clients.submissions), 1)

    async def test_errors_are_saved_as_final_assistant_messages(self):
        from langGraph_PoC.clients import WorkflowError

        clients, store = FakeClients(), memory()
        clients.extractions = [WorkflowError("Model unavailable")]
        result, _ = await self.run_cli(build_graph(clients), store, "Vacation", [])
        self.assertEqual(result["status"], "error")
        self.assertEqual((await store.load())[-1].content, "Model unavailable")


if __name__ == "__main__":
    unittest.main()
