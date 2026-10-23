"""Fixtures for the P11 backup and restore tests.

The backup reads the whole database, so these tests get a database of their own (one per
module), seeded with two tenants and one restaurant ready to take orders. The dump tools run
like the P2 restore test's (`VELORA_TEST_PG_TOOLS`), but connect as the backup role over
TCP, with the password passed in the environment.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

from tests.conversation.conftest import restaurant_shop
from tests.db import conftest as db_fixtures
from tests.db.conftest import BACKUP_ROLE, OWNER_ROLE, Cluster, PgDatabase
from tests.db.seed import seed_tenant
from tests.messaging.conftest import Shop
from velora.backup.config import PgTools
from velora.backup.crypto import generate_keypair, public_key_from_text, secret_key_from_text
from velora.db.migrate import migrate_up

cluster = db_fixtures.cluster


@dataclass(frozen=True)
class BackupDb:
    pg: PgDatabase
    shop: Shop

    @property
    def backup_url(self) -> str:
        return self.pg.cluster.url(self.pg.name, BACKUP_ROLE)


@pytest.fixture(scope="module")
def bkdb(cluster: Cluster) -> Iterator[BackupDb]:
    name = db_fixtures.new_database_name("backup")
    cluster.create_database(name)
    try:
        migrate_up(cluster.url(name, OWNER_ROLE))
        pg = PgDatabase(cluster, name)
        with pg.admin() as conn:
            seed_tenant(conn, "backup-a", "BA")
            seed_tenant(conn, "backup-b", "BB")
        yield BackupDb(pg, restaurant_shop(pg))
    finally:
        cluster.drop_database(name)


def tools_for(cluster: Cluster) -> PgTools:
    """The test's tool prefix, made to forward PGPASSWORD into a container."""
    prefix = shlex.split(os.environ.get("VELORA_TEST_PG_TOOLS", ""))
    if prefix[:2] == ["docker", "exec"]:
        return PgTools((*prefix[:-1], "-e", "PGPASSWORD", prefix[-1]), ("127.0.0.1", 5432))
    return PgTools(tuple(prefix))


@pytest.fixture(scope="module")
def tools(cluster: Cluster) -> PgTools:
    return tools_for(cluster)


@dataclass(frozen=True)
class Keys:
    public_text: str
    secret_text: str

    @property
    def public(self) -> X25519PublicKey:
        return public_key_from_text(self.public_text)

    @property
    def secret(self) -> X25519PrivateKey:
        return secret_key_from_text(self.secret_text)


@pytest.fixture(scope="module")
def keys() -> Keys:
    return Keys(*generate_keypair())


@pytest.fixture()
def store_dir(tmp_path: Path) -> Path:
    return tmp_path / "offsite"
