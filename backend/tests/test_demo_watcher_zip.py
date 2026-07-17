import asyncio
import io
import struct
import zipfile
import zlib
from pathlib import Path

import pytest

import app.demo_watcher as demo_watcher_module
from app.demo_watcher import (
    DemoWatcher,
    ZipProcessResult,
    _discover_demo_files_bounded,
    _extract_dems_from_zip_sync,
    _iter_local_header_zip_dems,
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


def test_discover_demo_files_bounded_stops_after_direct_children(tmp_path: Path):
    nested = tmp_path / "source" / "deep"
    nested.mkdir(parents=True)
    first = tmp_path / "source" / "first.dem"
    second = tmp_path / "SECOND.DEM"
    archive = tmp_path / "source" / "matches.ZIP"
    too_deep = nested / "ignored.dem"
    ignored = nested / "notes.txt"
    for path in (first, second, archive, too_deep, ignored):
        path.write_bytes(b"x")

    dems, zips, existing, visited_dirs, errors = _discover_demo_files_bounded(
        [tmp_path]
    )

    assert {path.name for path in dems} == {"first.dem", "SECOND.DEM"}
    assert [path.name for path in zips] == ["matches.ZIP"]
    assert existing == {str(first.resolve()), str(second.resolve())}
    assert visited_dirs == 2
    assert errors == 0


def test_scan_existing_uses_path_index_and_reports_new_files(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    known = source / "known.dem"
    fresh = source / "fresh.dem"
    known.write_bytes(b"known")
    fresh.write_bytes(b"fresh")

    class FakeDB:
        purged_args = None

        async def load_scan_path_index(self):
            return {str(known.resolve())}, set()

        async def purge_deleted_demo_files(self, existing_paths, roots):
            self.purged_args = (set(existing_paths), list(roots))
            return 0

    seen: list[Path] = []

    async def on_detected(path: Path, _origin_zip: str | None):
        seen.append(path)
        return True

    db = FakeDB()
    watcher = DemoWatcher([str(tmp_path)], on_detected, db)  # type: ignore[arg-type]
    report = asyncio.run(watcher.scan_existing())

    assert seen == [fresh.resolve()]
    assert report["roots_scanned"] == 1
    assert report["demos_found"] == 2
    assert report["new_demos"] == 1
    assert report["skipped_known"] == 1
    assert report["errors"] == 0
    assert db.purged_args is not None
    assert db.purged_args[0] == {str(known.resolve()), str(fresh.resolve())}


def test_scan_existing_does_not_purge_when_directory_enumeration_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeDB:
        purge_called = False

        async def load_scan_path_index(self):
            return set(), set()

        async def purge_deleted_demo_files(self, _existing_paths, _roots):
            self.purge_called = True
            return 0

    def failed_discovery(_roots):
        return [], [], set(), 1, 1

    monkeypatch.setattr(demo_watcher_module, "_discover_demo_files_bounded", failed_discovery)

    async def on_detected(_path: Path, _origin_zip: str | None):
        return True

    db = FakeDB()
    watcher = DemoWatcher([str(tmp_path)], on_detected, db)  # type: ignore[arg-type]
    report = asyncio.run(watcher.scan_existing())

    assert report["errors"] == 1
    assert report["purged_missing"] == 0
    assert db.purge_called is False


def test_scan_existing_does_not_repeat_directory_walk_for_zip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    archive = tmp_path / "match.zip"
    archive.write_bytes(b"not-empty")
    real_discovery = demo_watcher_module._discover_demo_files_bounded
    discovery_calls = 0

    def counted_discovery(roots, max_depth=1):
        nonlocal discovery_calls
        discovery_calls += 1
        return real_discovery(roots, max_depth=max_depth)

    class FakeDB:
        async def load_scan_path_index(self):
            return set(), set()

        async def purge_deleted_demo_files(self, _existing_paths, _roots):
            return 0

    async def on_detected(_path: Path, _origin_zip: str | None):
        return True

    async def unchanged_zip(*_args, **_kwargs):
        return ZipProcessResult()

    monkeypatch.setattr(demo_watcher_module, "_discover_demo_files_bounded", counted_discovery)
    watcher = DemoWatcher([str(tmp_path)], on_detected, FakeDB())  # type: ignore[arg-type]
    monkeypatch.setattr(watcher, "_on_raw_zip_detected", unchanged_zip)

    report = asyncio.run(watcher.scan_existing())

    assert discovery_calls == 1
    assert report["archives_found"] == 1
    assert report["errors"] == 0


def test_zip_extract_failure_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not-empty")

    def fail_extract(_path: Path):
        raise OSError("disk write failed")

    monkeypatch.setattr(demo_watcher_module, "_extract_dems_from_zip_sync", fail_extract)

    async def on_detected(_path: Path, _origin_zip: str | None):
        return True

    watcher = DemoWatcher([str(tmp_path)], on_detected)
    result = asyncio.run(
        watcher._on_raw_zip_detected(archive, enqueue_extracted=False, assume_stable=True)
    )

    assert result.extracted_paths == ()
    assert result.errors == 1


def test_scan_existing_rejects_zip_result_outside_depth_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "root"
    root.mkdir()
    archive = root / "match.zip"
    archive.write_bytes(b"not-empty")
    outside = tmp_path / "outside.dem"
    outside.write_bytes(b"demo")

    class FakeDB:
        async def load_scan_path_index(self):
            return set(), set()

        async def purge_deleted_demo_files(self, _existing_paths, _roots):
            return 0

    seen: list[Path] = []

    async def on_detected(path: Path, _origin_zip: str | None):
        seen.append(path)
        return True

    async def escaped_zip_result(*_args, **_kwargs):
        return ZipProcessResult((outside.resolve(),), 0)

    watcher = DemoWatcher([str(root)], on_detected, FakeDB())  # type: ignore[arg-type]
    monkeypatch.setattr(watcher, "_on_raw_zip_detected", escaped_zip_result)

    report = asyncio.run(watcher.scan_existing())

    assert seen == []
    assert report["demos_found"] == 0
    assert report["errors"] == 1
