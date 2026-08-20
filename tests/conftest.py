from __future__ import annotations

import logging
import random
from collections.abc import Generator

import pytest
from faker import Faker


@pytest.fixture(scope="session")
def fake(pytestconfig: pytest.Config) -> Generator[Faker]:
    """Session-scoped Faker with seeded randomness."""
    faker = Faker(["en_US", "ja_JP", "de_DE"])
    seed = getattr(pytestconfig.option, "randomly_seed", None)
    if seed is None:
        seed = int(
            __import__("os").environ.get(
                "PYTEST_FAKER_SEED",
                random.randint(0, 2**32),  # noqa: S311
            ),
        )
        logging.getLogger("faker").info("Faker seed: %s", seed)
    faker.seed_instance(seed)
    return faker
