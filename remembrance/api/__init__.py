from fastapi import APIRouter

from .routes_health import router as routes_health_router
from .routes_health import protected_router as routes_health_protected_router
from .routes_memory import router as routes_memory_router
from .routes_search import router as routes_search_router
from .routes_gate import router as routes_gate_router
from .routes_checkpoint import router as routes_checkpoint_router
from .routes_sources import router as routes_sources_router
from .routes_evolution import router as routes_evolution_router
from .routes_edges import router as routes_edges_router
from .routes_param_advice import router as routes_param_advice_router

__all__ = [
    "routes_health_router",
    "routes_health_protected_router",
    "routes_memory_router",
    "routes_search_router",
    "routes_gate_router",
    "routes_checkpoint_router",
    "routes_sources_router",
    "routes_evolution_router",
    "routes_edges_router",
    "routes_param_advice_router",
]
