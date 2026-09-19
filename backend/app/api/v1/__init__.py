from fastapi import APIRouter

from app.api.v1.conjunctions import router as conjunctions_router
from app.api.v1.orbits import router as orbits_router

router = APIRouter()
router.include_router(orbits_router)
router.include_router(conjunctions_router)
