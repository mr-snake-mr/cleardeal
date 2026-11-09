import os
import shutil
import tempfile
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

# Point the app at a throwaway database before any app module imports settings.
# A directory plus a path avoids holding an open file handle (SIM115).
_tmpdir = tempfile.mkdtemp(prefix="cleardeal-test-")
_db_path = os.path.join(_tmpdir, "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"

from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> Iterator[None]:
    """Run the real migrations once.

    Tests must exercise the schema that ships, not a create_all() approximation.
    This is also what proves the migration applies cleanly, and it fails the
    whole suite loudly if the migration is broken.
    """
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    yield
    shutil.rmtree(_tmpdir, ignore_errors=True)


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    """HTTPS base URL on purpose.

    Session cookies are `Secure` outside debug, and a `Secure` cookie is silently
    dropped over `http://`. A test client on `http://testserver` would therefore
    fail every authenticated request for a reason that has nothing to do with the
    code under test. Testing over `https` is also closer to the deployment.
    """
    with TestClient(create_app(), base_url="https://testserver") as c:
        yield c
