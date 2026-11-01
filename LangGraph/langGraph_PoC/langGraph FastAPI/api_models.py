"""HTTP contracts for the wrapper; the original graph models remain unchanged."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from langGraph_PoC.models import ApplicationRecord, NonBlankString


class StartWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: NonBlankString
    conversation_id: NonBlankString | None = None


class ResumeWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: NonBlankString | None = None
    answer: StrictBool | NonBlankString | None = None

    @model_validator(mode="after")
    def paired_answer(self) -> "ResumeWorkflow":
        if self.prompt_id is None and self.answer is None:
            if self.model_fields_set:
                raise ValueError("Use an empty object for recovery, without null answer fields")
        elif self.prompt_id is None or self.answer is None:
            raise ValueError("Supply both prompt_id and answer")
        return self


class WorkflowInterrupt(BaseModel):
    prompt_id: str
    kind: Literal["clarification", "confirmation"]
    question: str
    details: dict = Field(default_factory=dict)


class WorkflowResponse(BaseModel):
    thread_id: str
    conversation_id: str
    status: str
    message: str | None = None
    interrupt: WorkflowInterrupt | None = None
    application: ApplicationRecord | None = None
    warnings: list[str] = Field(default_factory=list)
