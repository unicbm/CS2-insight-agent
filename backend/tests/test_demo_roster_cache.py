import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main
from app.demo_db import DemoDB


def test_get_or_index_demo_roster_reuses_persisted_stats(monkeypatch):
    cached = [
        {
            "player_name": "alpha",
            "team_number": 3,
            "steam_id64": "76561198000000001",
            "account_id": "7",
            "kills": 20,
            "deaths": 10,
            "assists": 4,
            "kd": 2.0,
        },
    ]
    monkeypatch.setattr(main.demo_db, "list_demo_player_stats", AsyncMock(return_value=cached))
    index_mock = AsyncMock(side_effect=AssertionError("cache hit must not parse the Demo"))
    monkeypatch.setattr(main, "index_demo_player_stats", index_mock)

    result = asyncio.run(main.get_or_index_demo_roster(12, "match.dem"))

    assert result["cache_hit"] is True
    assert result["players"] == [{
        "name": "alpha",
        "player_name": "alpha",
        "team": 3,
        "team_number": 3,
        "team_name": None,
        "steam_id": "76561198000000001",
        "steam_id64": "76561198000000001",
        "steamid64": "76561198000000001",
        "account_id": "7",
        "user_id": None,
        "kills": 20,
        "deaths": 10,
        "assists": 4,
        "kd": 2.0,
    }]
    index_mock.assert_not_awaited()


def test_get_or_index_demo_roster_indexes_only_on_cache_miss(monkeypatch):
    monkeypatch.setattr(main.demo_db, "list_demo_player_stats", AsyncMock(return_value=[]))
    parsed = [
        {
            "name": "bravo",
            "team": 2,
            "steam_id": "76561198000000002",
            "user_id": 11,
            "kills": 14,
            "deaths": 12,
            "assists": 5,
        },
    ]
    index_mock = AsyncMock(
        return_value={
            "indexed": True,
            "player_count": 1,
            "players": parsed,
            "error": None,
        },
    )
    monkeypatch.setattr(main, "index_demo_player_stats", index_mock)

    result = asyncio.run(main.get_or_index_demo_roster(13, "match.dem"))

    assert result["cache_hit"] is False
    assert result["indexed"] is True
    assert result["players"][0]["name"] == "bravo"
    assert result["players"][0]["team_number"] == 2
    assert result["players"][0]["steam_id64"] == "76561198000000002"
    assert result["players"][0]["account_id"] == "39734274"
    assert result["players"][0]["user_id"] == "11"
    index_mock.assert_awaited_once_with(13, "match.dem")


def test_batch_demo_summary_uses_roster_cache(monkeypatch):
    monkeypatch.setattr(
        main.demo_db,
        "get_demo_list_items",
        AsyncMock(
            return_value=[
                {
                    "id": 21,
                    "path": "match.dem",
                    "filename": "match.dem",
                    "map_name": "de_nuke",
                    "players": [{"name": "stale-shape"}],
                    "result": None,
                },
            ],
        ),
    )
    roster = [{"name": "alpha", "team": 3, "steam_id": "76561198000000001"}]
    lookup_mock = AsyncMock(
        return_value={
            "players": roster,
            "cache_hit": True,
            "indexed": True,
            "error": None,
        },
    )
    monkeypatch.setattr(main, "get_or_index_demo_roster", lookup_mock)

    response = asyncio.run(main.batch_demo_summary(main.BatchSummaryBody(ids=[21])))

    assert response["items"][0]["players"] == roster
    lookup_mock.assert_awaited_once()


def test_get_or_index_demo_roster_single_flights_concurrent_misses(monkeypatch):
    state = {"indexed": False, "calls": 0}
    parsed = [{
        "name": "alpha",
        "team": 2,
        "steam_id": "76561198000000002",
        "user_id": 11,
        "kills": 14,
        "deaths": 12,
        "assists": 5,
    }]

    async def list_stats(_demo_id):
        if not state["indexed"]:
            return []
        return [{
            "player_name": "alpha",
            "team_number": 2,
            "steam_id64": "76561198000000002",
            "account_id": "39734274",
            "user_id": "11",
            "kills": 14,
            "deaths": 12,
            "assists": 5,
            "kd": 1.167,
        }]

    async def index_once(_demo_id, _demo_path):
        state["calls"] += 1
        await asyncio.sleep(0.02)
        state["indexed"] = True
        return {
            "indexed": True,
            "player_count": 1,
            "players": parsed,
            "error": None,
        }

    monkeypatch.setattr(main.demo_db, "list_demo_player_stats", list_stats)
    monkeypatch.setattr(main, "index_demo_player_stats", index_once)

    async def scenario():
        return await asyncio.gather(
            main.get_or_index_demo_roster(31337, "same.dem"),
            main.get_or_index_demo_roster(31337, "same.dem"),
        )

    first, second = asyncio.run(scenario())
    assert state["calls"] == 1
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True


def test_roster_cache_round_trip_preserves_public_contract(tmp_path):
    async def scenario():
        db = DemoDB(tmp_path / "roster.sqlite3")
        await db.init_db()
        demo_path = str(tmp_path / "roundtrip.dem")
        demo_id, _ = await db.add_demo(demo_path, status="done")
        await db.replace_demo_player_stats(
            demo_id,
            demo_path,
            [{
                "name": "alpha",
                "team": 2,
                "steam_id": "76561198000000002",
                "user_id": 11,
                "kills": 14,
                "deaths": 12,
                "assists": 5,
            }],
        )
        cached = await db.list_demo_player_stats(demo_id)
        return main._roster_rows_for_api(cached)

    players = asyncio.run(scenario())
    assert players == [{
        "name": "alpha",
        "player_name": "alpha",
        "team": 2,
        "team_number": 2,
        "team_name": None,
        "steam_id": "76561198000000002",
        "steam_id64": "76561198000000002",
        "steamid64": "76561198000000002",
        "account_id": "39734274",
        "user_id": "11",
        "kills": 14,
        "deaths": 12,
        "assists": 5,
        "kd": 1.167,
    }]
    assert not ({"id", "demo_id", "demo_path", "normalized_name", "indexed_at"} & players[0].keys())
