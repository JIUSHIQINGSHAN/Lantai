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
from .routes_retrieval import router as routes_retrieval_router
from .routes_verification import router as routes_verification_router
from .routes_candidates import router as routes_candidates_router
from .routes_dialogue import router as routes_dialogue_router
from .routes_digest import router as routes_digest_router
from .routes_conflicts import router as routes_conflicts_router
from .routes_scenes import router as routes_scenes_router
from .routes_obsidian import router as routes_obsidian_router
from .routes_import import router as routes_import_router
from .routes_tree import router as routes_tree_router
from .routes_crystals import router as routes_crystals_router
from .routes_graph import router as routes_graph_router
from .routes_recall_chain import router as routes_recall_chain_router
from .routes_ui import router as routes_ui_router
from .routes_work_items import router as routes_work_items_router
from .routes_persona import router as routes_persona_router

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
    "routes_retrieval_router",
    "routes_verification_router",
    "routes_candidates_router",
    "routes_dialogue_router",
    "routes_digest_router",
    "routes_conflicts_router",
    "routes_scenes_router",
    "routes_obsidian_router",
    "routes_import_router",
    "routes_tree_router",
    "routes_crystals_router",
    "routes_graph_router",
    "routes_recall_chain_router",
    "routes_ui_router",
    "routes_work_items_router",
    "routes_persona_router",
]

