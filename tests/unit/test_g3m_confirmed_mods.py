"""The list of mods the G3M creator confirmed install without Manual install."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from adapters.gamebanana_adapter import GameBananaAPI
from services.mod.filter_service import filter_and_sort_mods


@pytest.fixture(autouse=True)
def _restore_ids():
    previous = (
        GameBananaAPI.g3m_confirmed,
        GameBananaAPI._g3m_confirmed_loaded_at,
        GameBananaAPI._g3m_confirmed_etag,
    )
    GameBananaAPI._g3m_confirmed_etag = ""
    yield
    (
        GameBananaAPI.g3m_confirmed,
        GameBananaAPI._g3m_confirmed_loaded_at,
        GameBananaAPI._g3m_confirmed_etag,
    ) = previous


def _fetch(monkeypatch, payload=None, error=None, status=200, etag="v1"):
    response = Mock(status_code=status, headers={"ETag": etag}, json=Mock(return_value=payload))
    response.raise_for_status.side_effect = error
    get = Mock(return_value=response)
    monkeypatch.setattr("adapters.gamebanana_adapter.requests.get", get)
    GameBananaAPI.load_g3m_confirmed()
    return get


def test_list_is_read_from_the_mods_array(monkeypatch):
    _fetch(monkeypatch, {"mods": [{"id": "gb_mod_1", "note": " Chapter 1 only "}, {"id": "gb_mod_9"}, {"name": "no id"}, "junk"]})

    assert GameBananaAPI.g3m_confirmed == {"gb_mod_1": "Chapter 1 only", "gb_mod_9": ""}


def test_failed_fetch_keeps_the_previous_list(monkeypatch):
    GameBananaAPI.g3m_confirmed = {"gb_mod_1": ""}

    _fetch(monkeypatch, error=OSError("offline"))

    assert GameBananaAPI.g3m_confirmed == {"gb_mod_1": ""}


def test_fetch_is_a_single_short_attempt_and_reuses_an_unchanged_list(monkeypatch):
    get = _fetch(monkeypatch, {"mods": [{"id": "gb_mod_1"}]}, etag="v7")
    assert get.call_args.kwargs["timeout"] == (2, 3)

    get = _fetch(monkeypatch, status=304)

    assert get.call_args.kwargs["headers"] == {"If-None-Match": "v7"}
    assert GameBananaAPI.g3m_confirmed == {"gb_mod_1": ""}


def test_filter_keeps_only_confirmed_mods():
    GameBananaAPI.g3m_confirmed = {"gb_mod_2": ""}
    mods = [{"id": f"gb_mod_{number}", "game": "deltarune"} for number in (1, 2, 3)]

    result = filter_and_sort_mods(mods, {"only_g3m": True, "game": "deltarune"})

    assert [mod["id"] for mod in result] == ["gb_mod_2"]
