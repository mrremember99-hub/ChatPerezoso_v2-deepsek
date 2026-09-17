"""Plugin de búsqueda: grep recursivo sobre el workspace.

Sin dependencias externas. Todas las operaciones son de solo lectura.
"""
from .client import SearchClient, SearchError
from .provider import SearchProvider

__all__ = ["SearchClient", "SearchError", "SearchProvider"]
