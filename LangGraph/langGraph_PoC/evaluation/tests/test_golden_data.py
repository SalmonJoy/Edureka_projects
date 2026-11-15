"""Check reference labels and upload deduplication without any service calls."""

import copy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock
from uuid import UUID

from langsmith.schemas import Example, ExampleCreate

from emp_details.main import EMPLOYEES
from HR_policy.main import POLICY_CHUNKS
from langGraph_PoC.evaluation.dataset import DATA_FILE, load_examples, validate_examples
from langGraph_PoC.evaluation.upload_dataset import upload
from langGraph_PoC.models import Employee, LeaveDetails


class GoldenDataTests(unittest.TestCase):
    def test_25_self_contained_examples_use_current_fixtures_and_sdk_schema(self):
        rows = load_examples()
        self.assertEqual(len(DATA_FILE.read_text(encoding="utf-8").splitlines()), 25)
        self.assertEqual(len({row["metadata"]["category"] for row in rows}), 25)
        for row in rows:
            self.assertEqual(row["inputs"]["employee"], EMPLOYEES["EMP004"])
            self.assertEqual(row["inputs"]["policy_chunks"], [chunk.model_dump() for chunk in POLICY_CHUNKS])
            Employee.model_validate(row["inputs"]["employee"])
            if row["reference_outputs"]["leave"]:
                LeaveDetails.model_validate(row["reference_outputs"]["leave"])
            ExampleCreate(**row)

    def test_reference_weekdays_and_notice_are_checked_independently(self):
        for field in ["requested_days", "working_days_notice", "eligible"]:
            rows = copy.deepcopy(load_examples())
            rows[0]["reference_outputs"][field] = False if field == "eligible" else 99
            with self.assertRaises(ValueError):
                validate_examples(rows)

    def test_confirmation_and_history_do_not_silently_authorize_submission(self):
        rows = load_examples()
        approved = [row for row in rows if row["reference_outputs"]["expected_submission_calls"]]
        self.assertEqual([row["metadata"]["case_id"] for row in approved], ["LEAVE023"])
        self.assertIs(approved[0]["inputs"]["resume_answers"][0]["answer"], True)
        self.assertEqual(rows[24]["reference_outputs"]["status"], "awaiting_confirmation")
        self.assertEqual(rows[24]["inputs"]["resume_answers"], [])
        bad = copy.deepcopy(rows)
        bad[22]["inputs"]["resume_answers"][0]["answer"] = "yes"
        with self.assertRaisesRegex(ValueError, "strict Boolean"):
            validate_examples(bad)

    def test_zero_duplicate_or_missing_cases_are_rejected(self):
        rows = load_examples()
        with self.assertRaises(ValueError):
            validate_examples(rows[:-1])
        rows[-1]["metadata"]["case_id"] = "LEAVE001"
        with self.assertRaises(ValueError):
            validate_examples(rows)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.client = MagicMock()
        self.dataset = SimpleNamespace(id=UUID("c4a445df-03e8-49df-965b-dc9d4b959018"),
                                       name="test-golden", url="https://example.com/dataset")
        self.client.has_dataset.return_value = False
        self.client.create_dataset.return_value = self.dataset
        self.client.read_dataset.return_value = self.dataset
        self.client.list_examples.side_effect = lambda **kwargs: list(self.saved)

        def create_examples(*, dataset_id, examples):
            self.saved.extend(Example(id=row["id"], dataset_id=dataset_id,
                                      created_at=datetime.now(timezone.utc),
                                      inputs=copy.deepcopy(row["inputs"]),
                                      outputs=copy.deepcopy(row["reference_outputs"]))
                              for row in examples)
        self.client.create_examples.side_effect = create_examples

    def test_upload_twice_adds_exactly_25_examples_once(self):
        first = upload(self.client, dataset_name="test-golden")
        self.client.has_dataset.return_value = True
        second = upload(self.client, dataset_name="test-golden")
        self.assertEqual((first["example_count"], first["new_examples_uploaded"]), (25, 25))
        self.assertEqual((second["example_count"], second["new_examples_uploaded"]), (25, 0))
        self.client.create_dataset.assert_called_once()
        self.client.create_examples.assert_called_once()
        self.assertEqual(len({str(row.id) for row in self.saved}), 25)

    def test_existing_reference_changes_are_not_overwritten(self):
        upload(self.client, dataset_name="test-golden")
        self.client.has_dataset.return_value = True
        self.saved[0].outputs["requested_days"] = 99
        with self.assertRaisesRegex(ValueError, "differs"):
            upload(self.client, dataset_name="test-golden")
        self.client.create_examples.assert_called_once()


if __name__ == "__main__":
    unittest.main()
