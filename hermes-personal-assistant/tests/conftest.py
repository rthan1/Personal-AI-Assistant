import pytest
from cryptography.fernet import Fernet

from assistant.storage.db import connect
from assistant.storage.memories import MemoryRepo
from assistant.storage.pending_changes import PendingChangeRepo
from assistant.storage.users import UserRepo


@pytest.fixture
def conn():
    conn = connect(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def secret_key():
    return Fernet.generate_key().decode()


@pytest.fixture
def repo(conn, secret_key):
    return UserRepo(conn, secret_key)


@pytest.fixture
def memories(conn, secret_key):
    return MemoryRepo(conn, secret_key)


@pytest.fixture
def pending(conn):
    return PendingChangeRepo(conn)
