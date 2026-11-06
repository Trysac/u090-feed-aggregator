"""Behavior tests for the public API of feed_aggregator."""

from __future__ import annotations

import pytest

import feed_aggregator


def test_normalize_trims_and_lowercases() -> None:
    assert feed_aggregator.normalize("  Hello World ") == "hello world"


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_normalize_rejects_blank_input(value: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        feed_aggregator.normalize(value)


def test_version_is_semver() -> None:
    parts = feed_aggregator.__version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)
