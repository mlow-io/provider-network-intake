"""Provider/network source-schema recognition and evidence-preserving interpretation."""

from .catalog_store import CatalogSnapshot, CatalogStore
from .contracts import is_valid_npi
from .domain_rules import (
    assess_licensure_state_consistency,
    classify_phone_conflict,
    classify_regulatory_regime,
    is_hospital_based_taxonomy,
)
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
    "assess_licensure_state_consistency",
    "classify_phone_conflict",
    "classify_regulatory_regime",
    "is_hospital_based_taxonomy",
    "is_valid_npi",
]
__version__ = "0.1.0"
