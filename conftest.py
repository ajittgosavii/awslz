"""Pytest configuration.

Ensures the repo root is importable and isolates SQLite test runs so they never
touch the app's real database. When LZ_DATABASE_URL is set (the PostgreSQL CI
job), this leaves it alone and the tests run against that backend instead.
"""

import os
import pathlib
import tempfile

if not os.environ.get("LZ_DATABASE_URL"):
    _p = pathlib.Path(tempfile.gettempdir()) / "lz_ci_store_test.db"
    for _suffix in ("", "-wal", "-shm"):
        pathlib.Path(str(_p) + _suffix).unlink(missing_ok=True)
    os.environ["LZ_DB_PATH"] = str(_p)
