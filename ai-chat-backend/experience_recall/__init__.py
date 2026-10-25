"""Read-only, source-backed experience retrieval across domain authorities."""

from .service import auto_context, bookshelf_documents, search_records

__all__ = ["auto_context", "bookshelf_documents", "search_records"]
