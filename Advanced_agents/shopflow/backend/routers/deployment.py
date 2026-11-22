from typing import Annotated

from fastapi import APIRouter, Depends

from backend.models import DeploymentCheckRequest, DeploymentResult, DeploymentRules, Environment
from backend.services import check_deployment, deployment_rules
from backend.store import ReleaseStore, get_store

router = APIRouter(tags=["Deployment policy"])


@router.get("/deployment-rules/{environment}", response_model=DeploymentRules)
def rules(environment: Environment):
    """Read-only demonstration policies, not real organizational requirements."""
    return deployment_rules(environment)


@router.post("/releases/{release_id}/deployment-check", response_model=DeploymentResult)
def deployment_check(release_id: str, request: DeploymentCheckRequest,
                     store: Annotated[ReleaseStore, Depends(get_store)]):
    """Calculate current gates. No deployment, approval, or evidence mutation occurs."""
    return check_deployment(store.get_release(release_id), request.environment)
