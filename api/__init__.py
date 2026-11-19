"""
api
---
Production API package for Saptic AI.
Exports `api_router` which aggregates all modular production routers:
- /api/v1: Mobile App & Web Companion REST API (Chat, Voice, Tasks, Calendar, Contacts, Smart Lists)
- /api: Cognitive Memorae & Timeline Engine (Timeline, Facts, Serendipity, Health Radar)
"""

from fastapi import APIRouter
from api.v1 import router as v1_router
from api.frontier import router as frontier_router

api_router = APIRouter()
api_router.include_router(v1_router)
api_router.include_router(frontier_router)

__all__ = ["api_router", "v1_router", "frontier_router"]
