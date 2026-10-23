from fastapi import APIRouter

from app.schemas.visa import VisaPreviewInput
from app.visa import assess_visas, rules_catalog

router = APIRouter(prefix="/api/v1", tags=["Static visa rules"])


@router.get("/visa-rules")
def visa_rules():
    return rules_catalog()


@router.post("/visa-check")
def visa_preview(payload: VisaPreviewInput):
    return assess_visas(
        payload.destination,
        payload.travel_date,
        payload.end_date,
        [{"name": p.name, "travel_profile": p.profile.model_dump()} for p in payload.travellers],
        payload.purpose,
    )
