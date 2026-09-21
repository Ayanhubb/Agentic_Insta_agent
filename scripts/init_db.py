"""Create local SQLite tables: python scripts/init_db.py"""

from __future__ import annotations

from config import get_settings
from db.session import bootstrap_database


def main() -> None:
    settings = get_settings()
    engine, _factory = bootstrap_database(settings)
    print(f"Initialized database at {settings.resolved_database_url}")
    engine.dispose()


if __name__ == "__main__":
    main()
