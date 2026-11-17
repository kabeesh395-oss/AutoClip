import os
import tempfile
from pathlib import Path
import pytest

# Ensure all tests run against an isolated test database
_temp_dir = tempfile.TemporaryDirectory()
_test_db = Path(_temp_dir.name) / "test_autoclip.sqlite3"
os.environ["AUTOCLIP_DB_PATH"] = str(_test_db)

from autoclip_app.batch_manager import init_batch_tables

init_batch_tables(_test_db)


@pytest.fixture(autouse=True)
def clean_test_db():
    """Ensure database tables exist for each test."""
    init_batch_tables(_test_db)
    yield
