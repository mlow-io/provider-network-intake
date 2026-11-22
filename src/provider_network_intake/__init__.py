"""Provider/network source-schema recognition and evidence-preserving interpretation."""

from .catalog_store import CatalogSnapshot, CatalogStore
from .contracts import is_valid_npi
from .engine import Engine
from .errors import Conflict, IntakeError, ResourceLimit
from .models import Inspection, SourceContext
from .recipes import Variant

__all__ = [
    "CatalogSnapshot",
    "CatalogStore",
    "Conflict",
    "Engine",
    "Inspection",
    "IntakeError",
    "ResourceLimit",
    "SourceContext",
    "Variant",
    "is_valid_npi",
]
__version__ = "0.1.0"
