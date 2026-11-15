"""Explicitly upload the golden dataset; do not run the agent or submit leave."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid5

from langGraph_PoC.config import ENV_FILE
from langGraph_PoC import tracing
from langGraph_PoC.evaluation.dataset import DATA_FILE, DATASET_NAME, load_examples


def upload(client, *, dataset_name: str = DATASET_NAME) -> dict:
    examples = load_examples()
    if client.has_dataset(dataset_name=dataset_name):
        dataset = client.read_dataset(dataset_name=dataset_name)
    else:
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description=("25 synthetic, policy-grounded reference cases for the Edureka leave agent. "
                         "Frozen per-case Asia/Kolkata dates, EMP004 balance 12, all five HR chunks, "
                         "expected checkpoints, and strict confirmation. Use mocked leave submission."),
            metadata={"dataset_version": "1.0", "example_count": 25, "employee_id": "EMP004"},
        )
    existing = {str(example.id): example for example in client.list_examples(dataset_id=dataset.id)}
    desired = {str(uuid5(UUID(str(dataset.id)), example["metadata"]["case_id"])): example for example in examples}
    if set(existing) - set(desired):
        raise ValueError("The existing dataset contains unrelated examples; no examples were changed.")
    for example_id, remote in existing.items():
        local = desired[example_id]
        # ExampleCreate writes reference_outputs; the read-side Example schema
        # still exposes the saved reference answer as outputs.
        if remote.inputs != local["inputs"] or remote.outputs != local["reference_outputs"]:
            raise ValueError("An existing golden example differs; use a new dataset version instead of overwriting it.")
    missing = [{"id": example_id, **example} for example_id, example in desired.items() if example_id not in existing]
    if missing:
        client.create_examples(dataset_id=dataset.id, examples=missing)
    saved = list(client.list_examples(dataset_id=dataset.id))
    if len(saved) != 25 or {str(example.id) for example in saved} != set(desired):
        raise RuntimeError("Upload verification failed; inspect the dataset before running the command again.")
    for remote in saved:
        local = desired[str(remote.id)]
        if remote.inputs != local["inputs"] or remote.outputs != local["reference_outputs"]:
            raise RuntimeError("An uploaded example did not match the local reference data.")
    return {
        "dataset_name": dataset.name, "dataset_id": str(dataset.id), "url": dataset.url,
        "example_count": len(saved), "new_examples_uploaded": len(missing),
        "local_sha256": hashlib.sha256(DATA_FILE.read_bytes()).hexdigest(),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-name", default=DATASET_NAME)
    args = parser.parse_args()
    try:
        if not tracing.configure_tracing(ENV_FILE):
            raise ValueError("LangSmith configuration unavailable")
        result = upload(tracing._client, dataset_name=args.dataset_name)
        Path(__file__).with_name("langsmith_dataset.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0
    except Exception:
        print("Golden dataset upload failed. Check dataset access, connectivity, and whether its name is already in use. "
              "No agent workflow was executed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
