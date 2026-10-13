"""Official company registries: new filings for the legal entities behind
CS teams and tournament operators.

Sources: UK Companies House, Danish Erhvervsstyrelsen (CVR filings index),
Norwegian Brønnøysund, US SEC EDGAR. All free and official; only Companies
House needs a (free) API key.
"""

from .client import RegistryAuthError, RegistryBlockedError, RegistryError
from .service import RegistryService

__all__ = ["RegistryAuthError", "RegistryBlockedError", "RegistryError", "RegistryService"]
__version__ = "0.1.0"
