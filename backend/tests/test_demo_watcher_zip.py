import io
import asyncio
import struct
import zipfile
import zlib
from pathlib import Path

import pytest

from app.demo_db import DemoDB
from app.demo_watcher import (
    _collect_scan_index,
    _extract_dems_from_zip_sync,
    _iter_local_header_zip_dems,
    DemoWatcher,
)


def _build_local_header_only_zip(name: str, payload: bytes) -> bytes:
    """Build a single-entry deflate zip without central directory (5E-style)."""
    name_bytes = name.encode("utf-8")
    compressor = zlib.compressobj(level=1, wbits=-zlib.MAX_WBITS)
    compressed = compressor.compress(payload) + compressor.flush()
    header = struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50,
        20,
        0,
        8,
        0,
        0,
        0,
        len(compressed),
        len(payload),
        len(name_bytes),
        0,
    )
    return header + name_bytes + compressed


def test_iter_local_header_zip_dems_reads_payload_without_eocd():
    payload = b"HL2DEMO" + b"\x00" * 16
    raw = _build_local_header_only_zip("match.dem", payload)
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(io.BytesIO(raw), "r")

    members = _iter_local_header_zip_dems_from_bytes(raw)
    assert members == [("match.dem", payload)]


def _iter_local_header_zip_dems_from_bytes(raw: bytes) -> list[tuple[str, bytes]]:
    path = Path("local-header.zip")
    path.write_bytes(raw)
    try:
        return _iter_local_header_zip_dems(path)
    finally:
        path.unlink(missing_ok=True)


def test_extract_dems_from_zip_sync_falls_back_to_local_header(tmp_path: Path):
    payload = b"HL2DEMO" + b"\x00" * 32
    zip_path = tmp_path / "replay.zip"
    zip_path.write_bytes(_build_local_header_only_zip("replay.dem", payload))

    extracted = _extract_dems_from_zip_sync(zip_path)
    assert len(extracted) == 1
    assert extracted[0].read_bytes() == payload


def test_collect_scan_index_respects_configured_depth(tmp_path: Path):
    (tmp_path / "root.dem").write_bytes(b"root")
    level_one = tmp_path / "one"
    level_two = level_one / "two"
    level_two.mkdir(parents=True)
    (level_one / "one.dem").write_bytes(b"one")
    (level_two / "two.dem").write_bytes(b"two")
    (level_two / "bundle.zip").write_bytes(b"zip")

    depth_zero, archives_zero, _ = _collect_scan_index(tmp_path, 0)
    depth_one, _, _ = _collect_scan_index(tmp_path, 1)
    depth_all, archives_all, _ = _collect_scan_index(tmp_path, -1)

    assert {path.name for path in depth_zero} == {"root.dem"}
    assert archives_zero == []
    assert {path.name for path in depth_one} == {"root.dem", "one.dem"}
    assert {path.name for path in depth_all} == {"root.dem", "one.dem", "two.dem"}
    assert [path.name for path in archives_all] == ["bundle.zip"]


def test_collect_scan_index_does_not_mark_unreadable_or_missing_root_as_scanned(tmp_path: Path):
    demos, archives, scanned_directories = _collect_scan_index(tmp_path / "missing", 2)
    assert demos == []
    assert archives == []
    assert scanned_directories == set()


def test_watcher_event_scope_uses_same_depth_semantics(tmp_path: Path):
    async def on_detected(_path: Path, _origin_zip: str | None) -> None:
        return None

    nested = tmp_path / "one" / "two" / "match.dem"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"demo")

    depth_one = DemoWatcher([], on_detected, max_depth=1)
    depth_two = DemoWatcher([], on_detected, max_depth=2)
    depth_all = DemoWatcher([], on_detected, max_depth=-1)

    assert depth_one._event_path_in_scope(nested, tmp_path) is False
    assert depth_two._event_path_in_scope(nested, tmp_path) is True
    assert depth_all._event_path_in_scope(nested, tmp_path) is True


def test_purge_deleted_demo_files_is_scoped_to_scanned_directories(tmp_path: Path):
    async def scenario() -> None:
        db = DemoDB(tmp_path / "demos.sqlite3")
        await db.init_db()
        watched = tmp_path / "watched"
        outside = tmp_path / "outside"
        watched.mkdir()
        outside.mkdir()
        kept_path = str((watched / "kept.dem").resolve())
        deleted_path = str((watched / "deleted.dem").resolve())
        outside_path = str((outside / "outside.dem").resolve())
        for demo_path in (kept_path, deleted_path, outside_path):
            _, inserted = await db.add_demo(demo_path, status="pending")
            assert inserted

        removed = await db.purge_deleted_demo_files(
            {kept_path},
            scanned_directories={str(watched.resolve())},
        )

        assert removed == 1
        assert await db.get_demo_by_path(kept_path) is not None
        assert await db.get_demo_by_path(deleted_path) is None
        assert await db.get_demo_by_path(outside_path) is not None
        assert await db.all_demo_paths() == {kept_path, outside_path}

    asyncio.run(scenario())
