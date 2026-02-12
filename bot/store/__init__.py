"""Store package — SQLite-backed data store."""

from typing import Optional

from .sqlite_store import SQLiteStore, generate_special_code

__all__ = [
    "SQLiteStore",
    "generate_special_code",
    "init_store",
    "get_store",
]

store: Optional[SQLiteStore] = None


async def init_store(data_file: str) -> SQLiteStore:
    """Initialize the global store (async — creates tables)."""
    global store
    store = SQLiteStore(data_file)
    await store.initialize()
    return store


def get_store() -> SQLiteStore:
    """Get the global store instance."""
    if store is None:
        raise RuntimeError("Store not initialized. Call init_store() first.")
    return store
