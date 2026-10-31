"""Dummy HR policy retrieval API for LangGraph learning exercises."""

from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator


app = FastAPI(
    title="HR Policy API",
    description=(
        "A dummy retrieval API that returns the same five fictional HR policy "
        "chunks for every query. No similarity search is performed."
    ),
    version="1.0.0",
)


class PolicySearchRequest(BaseModel):
    query: str = Field(strict=True, min_length=1, description="User's HR policy question")

    @field_validator("query")
    @classmethod
    def require_nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Query must not be blank")
        return value


class PolicyChunk(BaseModel):
    chunk_id: str
    title: str
    content: str


class PolicySearchResponse(BaseModel):
    query: str
    chunks: list[PolicyChunk]


# Fictional placeholder policies, always returned in this order.
POLICY_CHUNKS = [
    PolicyChunk(
        chunk_id="HR001",
        title="Leave Policy",
        content=(
            "Employees receive 20 days of annual leave per year. Planned leave "
            "should be requested at least three working days in advance and "
            "requires manager approval. Employees should notify their manager "
            "as soon as possible when taking sick leave."
        ),
    ),
    PolicyChunk(
        chunk_id="HR002",
        title="Working Hours Policy",
        content=(
            "Standard working hours are Monday through Friday, 9:00 AM to "
            "6:00 PM, with a one-hour lunch break. Employees should coordinate "
            "changes to their schedule with their manager and attend agreed "
            "team meetings."
        ),
    ),
    PolicyChunk(
        chunk_id="HR003",
        title="Remote Work Policy",
        content=(
            "Eligible employees may work remotely up to two days per week "
            "with manager approval. Remote employees must remain available "
            "during agreed working hours, use approved company systems, and "
            "protect confidential information."
        ),
    ),
    PolicyChunk(
        chunk_id="HR004",
        title="Expense Reimbursement Policy",
        content=(
            "Reasonable business expenses require manager approval. Employees "
            "must submit an expense claim with itemized receipts within 30 "
            "days of the expense. Approved claims are reimbursed in the next "
            "monthly payment cycle."
        ),
    ),
    PolicyChunk(
        chunk_id="HR005",
        title="Employee Conduct Policy",
        content=(
            "Employees must treat colleagues respectfully, avoid harassment "
            "and discrimination, and keep company and employee information "
            "confidential. Conflicts of interest and workplace concerns "
            "should be reported to Human Resources."
        ),
    ),
]


@app.post("/policies/search", response_model=PolicySearchResponse)
def search_policies(request: PolicySearchRequest) -> PolicySearchResponse:
    """Echo the query and return all five fixed chunks, without ranking them."""
    return PolicySearchResponse(query=request.query, chunks=POLICY_CHUNKS)
