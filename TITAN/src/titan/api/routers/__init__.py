"""Dashboard API routers (mounted under ``/api/v1``)."""

from titan.api.routers.admin import router as admin_router
from titan.api.routers.auth import router as auth_router
from titan.api.routers.business import router as business_router

__all__ = ["admin_router", "auth_router", "business_router"]
