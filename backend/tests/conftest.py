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
# Not the development default: `create_app` refuses to boot with that secret
# unless `debug` is on, and the test suite should exercise the production guard
# rather than the exemption from it.
os.environ["SESSION_SECRET"] = "test-secret-2f8c1d4e6a9b0c3f5e7d1a2b4c6d8e0f"
# 32 bytes of entropy, hex-encoded: the shape a real deployment should use.
os.environ["CREDENTIALS_ENCRYPTION_KEY"] = (
    "6f1c2b7d4e8a90c3f5d6b7a1e2c4d8f0a3b5c7d9e1f2a4b6c8d0e2f4a6b8c0d2"
)

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

@pytest.fixture(autouse=True)
def _clean_database():
    """Empty every table after each test, children first.

    Each module used to clean up its own rows, and that worked until a module
    started creating rows another module's cleanup could not delete: `test_auth`
    deleted users while `test_analysis` had left a project pointing at one, and
    the foreign key refused. One ordered teardown in one place removes the
    ordering problem from every test author's head.
    """
    from sqlalchemy import delete

    from app.database import SessionLocal
    from app.models import (
        Agreement,
        AgreementSection,
        AgreementVersion,
        AiRun,
        AuditEvent,
        ChangeItem,
        ChangeRequest,
        Invitation,
        MerchantAccount,
        Milestone,
        Payment,
        Project,
        User,
        WebhookEvent,
    )

    yield

    session = SessionLocal()
    try:
        for model in (
            AuditEvent,
            AiRun,
            WebhookEvent,
            Payment,
            ChangeItem,
            ChangeRequest,
            Milestone,
            AgreementSection,
            AgreementVersion,
            Agreement,
            Invitation,
            MerchantAccount,
            Project,
            User,
        ):
            session.execute(delete(model))
        session.commit()
    finally:
        session.close()