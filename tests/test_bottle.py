"""Bottle name validation and path safety."""

from __future__ import annotations

import pytest

from metalplay.bottle.manager import BottleError, bottle_path, validate_bottle_name


def test_validate_accepts_simple_names() -> None:
    assert validate_bottle_name("gaming") == "gaming"
    assert validate_bottle_name("steam") == "steam"
    assert validate_bottle_name("my.bottle_1") == "my.bottle_1"


@pytest.mark.parametrize(
    "name",
    ["", "  ", "..", "../steam", "foo/bar", "foo\\bar", ".hidden"],
)
def test_validate_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(BottleError):
        validate_bottle_name(name)


def test_bottle_path_stays_under_bottles_root() -> None:
    root = bottle_path("gaming").resolve()
    assert root.name == "gaming"
    assert "bottles" in root.parts
