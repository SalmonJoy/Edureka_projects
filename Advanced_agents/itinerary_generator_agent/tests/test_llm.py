import json
from types import SimpleNamespace

import pytest

from agent.errors import ServiceError
from agent.llm import StructuredLLM
from agent.models import PlanDecision
from conftest import sample_plan


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    def invoke(self, messages):
        self.messages.append(list(messages))
        return SimpleNamespace(content=next(self.responses))


def test_json_repair_is_bounded_and_supplies_validation_errors(settings):
    valid = json.dumps({"itinerary": sample_plan(), "reason": "A feasible plan."})
    model = ScriptedModel(["bad JSON", valid])
    result = StructuredLLM(settings, model).structured(PlanDecision, "Plan a trip", {})
    assert result.itinerary.hotel_id == "jaipur-haveli"
    assert len(model.messages) == 2
    assert "Repair the JSON" in model.messages[1][-1][1]
    assert "override memories" in model.messages[0][0][1]


def test_invalid_json_stops_after_one_repair(settings):
    model = ScriptedModel(["bad", "still bad"])
    with pytest.raises(ServiceError, match="one JSON repair"):
        StructuredLLM(settings, model).structured(PlanDecision, "Plan", {})
    assert len(model.messages) == 2


def test_fenced_json_is_accepted(settings):
    text = '```json\n' + json.dumps({"itinerary": None, "reason": "No feasible hotel."}) + '\n```'
    assert StructuredLLM(settings, ScriptedModel([text])).structured(PlanDecision, "Plan", {}).itinerary is None


def test_model_errors_are_sanitized(settings):
    class Broken:
        def invoke(self, messages):
            raise RuntimeError("secret-provider-response")

    with pytest.raises(ServiceError) as caught:
        StructuredLLM(settings, Broken()).structured(PlanDecision, "Plan", {})
    assert "secret-provider-response" not in str(caught.value)


@pytest.mark.parametrize("content", ["", None, []])
def test_empty_or_non_text_outputs_are_errors(settings, content):
    with pytest.raises(ServiceError, match="no usable final text"):
        StructuredLLM(settings, ScriptedModel([content])).structured(PlanDecision, "Plan", {})
