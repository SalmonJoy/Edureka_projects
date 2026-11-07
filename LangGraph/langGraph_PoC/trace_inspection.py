"""Read saved traces through SmithDB v2 APIs; never run or submit leave."""

import argparse
import asyncio
from datetime import datetime
import json

from langGraph_PoC.config import ENV_FILE
from langGraph_PoC import tracing


FIELDS = ["ID", "NAME", "RUN_TYPE", "STATUS", "PROJECT_ID", "TRACE_ID",
          "START_TIME", "END_TIME", "METADATA", "IS_ROOT", "PARENT_RUN_IDS"]


async def inspect_run(client, *, project_id: str, run_id: str) -> dict:
    run = await client.runs.retrieve(run_id, project_id=project_id, selects=FIELDS)
    link = await client.runs.get_url(
        run_id, project_id=project_id, trace_id=str(run.trace_id),
        **({"start_time": run.start_time.isoformat()} if run.start_time else {}),
    )
    return {"run": run.model_dump(mode="json"), "url": link.url}


async def query_workflow_runs(client, *, project_id: str, thread_id: str,
                              min_start_time: datetime) -> list[dict]:
    if min_start_time.tzinfo is None or min_start_time.utcoffset() is None:
        raise ValueError("An explicit timezone is required for the query start time.")
    if not thread_id.strip():
        raise ValueError("A workflow thread ID is required.")
    runs, seen = [], set()
    cursor = None
    while True:
        page = await client.runs.query(
            project_ids=[project_id], min_start_time=min_start_time,
            filter='and(eq(metadata_key, "thread_id"), eq(metadata_value, '
                   + json.dumps(thread_id) + '))',
            selects=FIELDS, page_size=100, **({"cursor": cursor} if cursor else {}),
        )
        runs.extend(item.model_dump(mode="json") for item in page.items or [])
        cursor = page.next_cursor
        if not cursor:
            return runs
        if cursor in seen:
            raise RuntimeError("LangSmith returned a repeated pagination cursor.")
        seen.add(cursor)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--run-id")
    selector.add_argument("--thread-id")
    parser.add_argument("--since", help="Required with --thread-id: ISO timestamp with timezone")
    args = parser.parse_args()
    if args.thread_id and not args.since:
        parser.error("--thread-id requires --since (queries must use an explicit time window)")
    try:
        if not tracing.configure_tracing(ENV_FILE) or not tracing._project_id:
            raise ValueError("LangSmith configuration or project UUID is unavailable")
        if args.run_id:
            result = await inspect_run(tracing._client, project_id=tracing._project_id, run_id=args.run_id)
        else:
            result = await query_workflow_runs(
                tracing._client, project_id=tracing._project_id, thread_id=args.thread_id,
                min_start_time=datetime.fromisoformat(args.since.replace("Z", "+00:00")),
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        print("LangSmith trace inspection failed. Check IDs, time range, permissions, and connectivity.")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
