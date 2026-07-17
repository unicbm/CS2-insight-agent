"""Watch multiple directories and enqueue new .dem files."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import struct
import time
import zlib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Iterable, Literal, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

if TYPE_CHECKING:
    from .demo_db import DemoDB

from .file_hash import file_md5_hex

logger = logging.getLogger(__name__)

DemoDetectionStatus = bool | Literal["duplicate"] | None
OnDemoDetected = Callable[[Path, Optional[str]], Awaitable[DemoDetectionStatus]]

_LOCAL_ZIP_SIG = b"PK\x03\x04"


@dataclass(frozen=True)
class ZipProcessResult:
    extracted_paths: tuple[Path, ...] = ()
    errors: int = 0


def _sort_paths_by_mtime_newest_first(paths: Iterable[Path]) -> list[Path]:
    """按文件修改时间降序（最近改动的优先），stat 失败置末。"""
    scored: list[tuple[int, str, Path]] = []
    for path in paths:
        try:
            st = path.stat()
            ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
        except OSError:
            ns = -1
        scored.append((ns, path.name.casefold(), path))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [t[2] for t in scored]


def _path_key(path: str | os.PathLike[str]) -> str:
    """Stable path key for de-duplicating overlapping Windows scan roots."""
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(path))))


def _discover_demo_files_bounded(
    roots: Iterable[Path],
    max_depth: int = 1,
) -> tuple[list[Path], list[Path], set[str], int, int]:
    """Enumerate demo candidates in one bounded scandir pass.

    Depth 0 is the selected root and depth 1 its direct child directories.
    Directory symlinks are deliberately not followed: user-selected roots may
    contain junction cycles or point far outside the intended scan scope.
    Returns ``(dems, zips, resolved_demo_paths, visited_dirs, errors)``.
    """
    dems: dict[str, tuple[int, Path]] = {}
    zips: dict[str, tuple[int, Path]] = {}
    visited_dirs = 0
    errors = 0
    depth_limit = max(0, int(max_depth))

    for root in roots:
        stack = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            visited_dirs += 1
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            entry_path = Path(entry.path)
                            is_junction = getattr(entry_path, "is_junction", None)
                            if entry.is_symlink() or (callable(is_junction) and is_junction()):
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if depth < depth_limit:
                                    stack.append((entry_path, depth + 1))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            suffix = os.path.splitext(entry.name)[1].lower()
                            if suffix not in (".dem", ".zip"):
                                continue
                            st = entry.stat(follow_symlinks=False)
                            mtime_ns = int(
                                getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
                            )
                            path = entry_path.resolve()
                            target = dems if suffix == ".dem" else zips
                            target.setdefault(_path_key(path), (mtime_ns, path))
                        except OSError:
                            errors += 1
            except OSError:
                errors += 1

    def newest_first(values: Iterable[tuple[int, Path]]) -> list[Path]:
        return [
            item[1]
            for item in sorted(
                values,
                key=lambda item: (item[0], item[1].name.casefold()),
                reverse=True,
            )
        ]

    dem_paths = newest_first(dems.values())
    zip_paths = newest_first(zips.values())
    return dem_paths, zip_paths, {str(path) for path in dem_paths}, visited_dirs, errors


def _safe_zip_member_name(name: str) -> str | None:
    """Return basename of a .dem member, or None if unsafe."""
    if not name or name.endswith("/"):
        return None
    parts = name.replace("\\", "/").split("/")
    if not parts or any(p == ".." for p in parts):
        return None
    base = parts[-1]
    if not base.lower().endswith(".dem"):
        return None
    return base


def _iter_local_header_zip_dems(zip_path: Path) -> list[tuple[str, bytes]]:
    """Parse .dem payloads from ZIP local headers when EOCD is missing (e.g. some 5E replays)."""
    try:
        data = zip_path.read_bytes()
    except OSError:
        return []
    if not data.startswith(_LOCAL_ZIP_SIG):
        return []
    out: list[tuple[str, bytes]] = []
    offset = 0
    while offset + 30 <= len(data) and data[offset : offset + 4] == _LOCAL_ZIP_SIG:
        fn_len = struct.unpack_from("<H", data, offset + 26)[0]
        extra_len = struct.unpack_from("<H", data, offset + 28)[0]
        header_end = offset + 30 + fn_len + extra_len
        if header_end > len(data):
            break
        name = data[offset + 30 : offset + 30 + fn_len].decode("utf-8", "replace")
        method = struct.unpack_from("<H", data, offset + 8)[0]
        csize = struct.unpack_from("<I", data, offset + 18)[0]
        usize = struct.unpack_from("<I", data, offset + 22)[0]
        base = _safe_zip_member_name(name)
        payload_end = header_end + csize if csize else len(data)
        if payload_end > len(data):
            logger.warning("Truncated local-header zip payload in %s", zip_path)
            break
        payload = data[header_end:payload_end]
        if base:
            try:
                if method == 8:
                    raw = zlib.decompressobj(-zlib.MAX_WBITS).decompress(payload)
                elif method == 0:
                    raw = payload
                else:
                    logger.warning("Unsupported zip compression method %s in %s", method, zip_path)
                    break
            except Exception:
                logger.exception("Failed to decompress local-header zip member %s from %s", name, zip_path)
                break
            if usize and len(raw) != usize:
                logger.warning(
                    "Local-header zip size mismatch for %s: got %d expected %d",
                    zip_path,
                    len(raw),
                    usize,
                )
            out.append((base, raw))
        if payload_end >= len(data):
            break
        offset = payload_end
    return out


def _reuse_existing_dem_if_same_size(dest_dir: Path, base: str, size: int) -> Path | None:
    existing_target = dest_dir / base
    if existing_target.is_symlink() or not existing_target.is_file():
        return None
    try:
        if existing_target.stat().st_size == size:
            logger.info("Demo 已解压（同名且大小一致），跳过: %s", existing_target)
            return existing_target.resolve()
    except OSError:
        pass
    return None


def _pick_extract_path(dest_dir: Path, member_base: str, zip_path: Path) -> Path:
    """Avoid overwriting an existing .dem in the watch folder."""
    stem = Path(member_base).stem
    first = dest_dir / member_base
    if not first.is_file():
        return first
    for i in range(1, 1000):
        cand = dest_dir / f"{stem}_fromzip_{zip_path.stem}_{i}.dem"
        if not cand.is_file():
            return cand
    return dest_dir / f"{stem}_fromzip_{zip_path.stem}_{int(time.time() * 1000)}.dem"


def _zip_extract_outputs_present(zip_path: Path) -> bool:
    """5E 等平台通常解压为与 zip 同 stem 的 .dem；用于判断 skip extract 是否安全。"""
    return zip_path.with_suffix(".dem").is_file()


def _extract_dems_from_zip_sync(zip_path: Path) -> list[Path]:
    """Extract all .dem from zip into the same directory as the zip. Returns written paths."""
    out: list[Path] = []
    dest_dir = zip_path.parent
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = [m for m in zf.namelist() if _safe_zip_member_name(m)]
            if not members:
                return out
            for m in members:
                base = _safe_zip_member_name(m)
                if not base:
                    continue
                info = zf.getinfo(m)
                reused = _reuse_existing_dem_if_same_size(dest_dir, base, info.file_size)
                if reused:
                    out.append(reused)
                    continue
                target = _pick_extract_path(dest_dir, base, zip_path)
                with zf.open(m, "r") as src, target.open("wb") as dst:
                    dst.write(src.read())
                out.append(target.resolve())
        return out
    except zipfile.BadZipFile:
        logger.info("Standard zip parse failed, trying local-header fallback: %s", zip_path)
    for base, raw in _iter_local_header_zip_dems(zip_path):
        reused = _reuse_existing_dem_if_same_size(dest_dir, base, len(raw))
        if reused:
            out.append(reused)
            continue
        target = _pick_extract_path(dest_dir, base, zip_path)
        target.write_bytes(raw)
        out.append(target.resolve())
    return out


def _demo_ingest_md5_enabled() -> bool:
    v = (os.environ.get("CS2_INSIGHT_DISABLE_DEMO_MD5") or "").strip().lower()
    return v not in ("1", "true", "yes")


def _extract_zip_dems_dedupe_sync(zip_path: Path, existing_md5s: frozenset[str]) -> list[Path]:
    """解压 zip 内 .dem；若与库中已有 content_md5 相同则不落盘（避免重复内容与重复解析）。"""
    out: list[Path] = []
    seen: set[str] = set(existing_md5s)
    dest_dir = zip_path.parent

    def _write_deduped(base: str, raw: bytes, size_hint: int | None = None) -> None:
        reused = _reuse_existing_dem_if_same_size(dest_dir, base, size_hint if size_hint is not None else len(raw))
        if reused:
            out.append(reused)
            return
        target = _pick_extract_path(dest_dir, base, zip_path)
        h = hashlib.md5()
        h.update(raw)
        md5_hex = h.hexdigest()
        if md5_hex in seen:
            return
        target.write_bytes(raw)
        seen.add(md5_hex)
        out.append(target.resolve())

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = [m for m in zf.namelist() if _safe_zip_member_name(m)]
            if not members:
                return out
            for m in members:
                base = _safe_zip_member_name(m)
                if not base:
                    continue
                info = zf.getinfo(m)
                reused = _reuse_existing_dem_if_same_size(dest_dir, base, info.file_size)
                if reused:
                    out.append(reused)
                    continue
                target = _pick_extract_path(dest_dir, base, zip_path)
                h = hashlib.md5()
                try:
                    with zf.open(m, "r") as src, target.open("wb") as dst:
                        while True:
                            chunk = src.read(1024 * 1024)
                            if not chunk:
                                break
                            h.update(chunk)
                            dst.write(chunk)
                except Exception:
                    try:
                        if target.is_file():
                            target.unlink()
                    except OSError:
                        pass
                    raise
                md5_hex = h.hexdigest()
                if md5_hex in seen:
                    try:
                        if target.is_file():
                            target.unlink()
                    except OSError:
                        pass
                    continue
                seen.add(md5_hex)
                out.append(target.resolve())
        return out
    except zipfile.BadZipFile:
        logger.info("Standard zip parse failed, trying local-header fallback: %s", zip_path)
    for base, raw in _iter_local_header_zip_dems(zip_path):
        _write_deduped(base, raw)
    return out


class _DemoEventHandler(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop, watcher: "DemoWatcher") -> None:
        super().__init__()
        self._loop = loop
        self._watcher = watcher

    def on_created(self, event) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(event.src_path)
        if not self._watcher._path_within_scan_depth(path):
            return
        suf = path.suffix.lower()
        if suf == ".dem":
            asyncio.run_coroutine_threadsafe(self._watcher._on_raw_dem_detected(path), self._loop)
        elif suf == ".zip":
            asyncio.run_coroutine_threadsafe(self._watcher._on_raw_zip_detected(path), self._loop)


class DemoWatcher:
    def __init__(
        self,
        paths: list[str],
        on_detected: OnDemoDetected,
        demo_db: Optional["DemoDB"] = None,
    ) -> None:
        self._paths = paths
        self._on_detected = on_detected
        self._demo_db = demo_db
        self._observer: Observer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # 同一 zip 被「目录扫描 + 文件监听」或并发协程同时处理时，会在解压竞态下重复生成 _fromzip_*_N.dem
        self._zip_extract_lock = asyncio.Lock()

    def _normalized_paths(self) -> list[Path]:
        out: list[Path] = []
        seen: set[str] = set()
        for p in self._paths:
            if not p:
                continue
            cand = Path(p).expanduser()
            if not cand.is_dir():
                continue
            key = str(cand.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(cand)
        return out

    def _path_within_scan_depth(self, path: Path, max_depth: int = 1) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return False
        for root in self._normalized_paths():
            try:
                relative = resolved.relative_to(root.resolve())
            except (OSError, ValueError):
                continue
            parent_depth = max(0, len(relative.parts) - 1)
            if parent_depth <= max_depth:
                return True
        return False

    async def _wait_until_stable(self, path: Path, timeout_sec: int = 30) -> bool:
        prev_size = -1
        stable_count = 0
        checks = max(1, timeout_sec)
        for _ in range(checks):
            if not path.exists():
                await asyncio.sleep(1)
                continue
            try:
                size = path.stat().st_size
            except OSError:
                await asyncio.sleep(1)
                continue
            if size > 0 and size == prev_size:
                stable_count += 1
                if stable_count >= 2:
                    return True
            else:
                stable_count = 0
            prev_size = size
            await asyncio.sleep(1)
        return False

    async def _on_raw_dem_detected(self, path: Path) -> None:
        if not await self._wait_until_stable(path):
            logger.warning("Demo file not stable, skip: %s", path)
            return
        await self._on_detected(path, None)

    async def _on_raw_zip_detected(
        self,
        path: Path,
        *,
        enqueue_extracted: bool = True,
        assume_stable: bool = False,
    ) -> ZipProcessResult:
        processing_errors = 0
        # 目录批量扫描时文件早已落盘，跳过「每秒轮询等稳定」以免每个 zip 白等数秒
        if assume_stable:
            try:
                if path.stat().st_size <= 0:
                    logger.warning("Zip empty, skip: %s", path)
                    return ZipProcessResult(errors=1)
            except OSError as e:
                logger.warning("Cannot stat zip, skip: %s (%s)", path, e)
                return ZipProcessResult(errors=1)
        elif not await self._wait_until_stable(path):
            logger.warning("Zip file not stable, skip: %s", path)
            return ZipProcessResult(errors=1)
        try:
            zip_resolved = str(path.resolve())
        except OSError as e:
            logger.warning("Cannot resolve zip path, skip: %s (%s)", path, e)
            return ZipProcessResult(errors=1)
        async with self._zip_extract_lock:
            try:
                st = path.stat()
            except OSError as e:
                logger.warning("Cannot stat zip, skip: %s (%s)", path, e)
                return ZipProcessResult(errors=1)
            mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
            size_b = int(st.st_size)
            loop = asyncio.get_running_loop()
            col_md5 = self._demo_db is not None and getattr(self._demo_db, "ingest_md5_supported", False)
            dedupe_md5 = col_md5 and _demo_ingest_md5_enabled()
            if self._demo_db is not None:
                try:
                    if col_md5:
                        st_row = await self._demo_db.get_zip_extract_state(zip_resolved)
                        if st_row and dedupe_md5:
                            zip_md5_stored = (st_row.get("zip_md5") or "").strip()
                            if zip_md5_stored:
                                zm = await loop.run_in_executor(None, file_md5_hex, path)
                                if zm == zip_md5_stored:
                                    if _zip_extract_outputs_present(path):
                                        logger.info("Zip unchanged (md5), skip extract: %s", path)
                                        await self._demo_db.record_zip_extracted(
                                            zip_resolved,
                                            mtime_ns,
                                            size_b,
                                            zip_md5=zm,
                                        )
                                        return ZipProcessResult(errors=processing_errors)
                                    logger.info(
                                        "Zip unchanged (md5) but extracted .dem missing, re-extract: %s",
                                        path,
                                    )
                            if st_row and not zip_md5_stored:
                                if int(st_row["mtime_ns"]) == mtime_ns and int(st_row["size_bytes"]) == size_b:
                                    if _zip_extract_outputs_present(path):
                                        zm = await loop.run_in_executor(None, file_md5_hex, path)
                                        await self._demo_db.record_zip_extracted(
                                            zip_resolved,
                                            mtime_ns,
                                            size_b,
                                            zip_md5=zm,
                                        )
                                        logger.info(
                                            "Zip unchanged (mtime+size), skip extract; backfilled zip_md5: %s",
                                            path,
                                        )
                                        return ZipProcessResult(errors=processing_errors)
                                    logger.info(
                                        "Zip unchanged (mtime+size) but extracted .dem missing, re-extract: %s",
                                        path,
                                    )
                        if not dedupe_md5 and await self._demo_db.zip_unchanged_since_extract(
                            zip_resolved,
                            mtime_ns,
                            size_b,
                        ):
                            if _zip_extract_outputs_present(path):
                                zm = await loop.run_in_executor(None, file_md5_hex, path)
                                await self._demo_db.record_zip_extracted(
                                    zip_resolved,
                                    mtime_ns,
                                    size_b,
                                    zip_md5=zm,
                                )
                                logger.info(
                                    "Zip unchanged (mtime+size), skip extract; backfilled zip_md5: %s",
                                    path,
                                )
                                return ZipProcessResult(errors=processing_errors)
                            logger.info(
                                "Zip unchanged (mtime+size) but extracted .dem missing, re-extract: %s",
                                path,
                            )
                    else:
                        if await self._demo_db.zip_unchanged_since_extract(zip_resolved, mtime_ns, size_b):
                            if _zip_extract_outputs_present(path):
                                logger.info("Zip unchanged since last extract, skip re-import: %s", path)
                                return ZipProcessResult(errors=processing_errors)
                            logger.info(
                                "Zip unchanged since last extract but .dem missing, re-extract: %s",
                                path,
                            )
                except Exception:
                    logger.exception("zip_extract_state / md5 check failed for %s", path)
                    processing_errors += 1

            try:
                if col_md5 and self._demo_db is not None:
                    existing = frozenset(await self._demo_db.all_content_md5_hexes())
                    extracted = await loop.run_in_executor(None, _extract_zip_dems_dedupe_sync, path, existing)
                else:
                    extracted = await loop.run_in_executor(None, _extract_dems_from_zip_sync, path)
            except Exception:
                logger.exception("Failed to extract zip: %s", path)
                return ZipProcessResult(errors=processing_errors + 1)
            zip_md5_val: str | None = None
            if col_md5:
                try:
                    zip_md5_val = await loop.run_in_executor(None, file_md5_hex, path)
                except Exception:
                    logger.exception("zip md5 after extract failed: %s", path)
                    processing_errors += 1
            if self._demo_db is not None:
                try:
                    await self._demo_db.record_zip_extracted(
                        zip_resolved,
                        mtime_ns,
                        size_b,
                        zip_md5=zip_md5_val,
                    )
                except Exception:
                    logger.exception("record_zip_extracted failed for %s", path)
                    processing_errors += 1
            if not extracted:
                logger.info("Zip contains no new .dem files (or empty), skip: %s", path)
                return ZipProcessResult(errors=processing_errors)
            logger.info("Extracted %d .dem from zip %s", len(extracted), path)
            if enqueue_extracted:
                for dem in extracted:
                    try:
                        await self._on_detected(dem, zip_resolved)
                    except Exception:
                        logger.exception("Failed to enqueue demo extracted from %s: %s", path, dem)
                        processing_errors += 1
            return ZipProcessResult(tuple(extracted), processing_errors)

    async def start(self) -> None:
        if self._observer is not None:
            return
        self._loop = asyncio.get_running_loop()
        paths = self._normalized_paths()
        if not paths:
            logger.info("No demo watch paths configured, watcher idle")
            return
        handler = _DemoEventHandler(self._loop, self)
        observer = Observer()
        watch_dirs: dict[str, Path] = {}
        for root in paths:
            watch_dirs.setdefault(_path_key(root), root)
            try:
                with os.scandir(root) as entries:
                    for entry in entries:
                        entry_path = Path(entry.path)
                        is_junction = getattr(entry_path, "is_junction", None)
                        if entry.is_symlink() or (callable(is_junction) and is_junction()):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            watch_dirs.setdefault(_path_key(entry_path), entry_path)
            except OSError:
                logger.exception("Cannot enumerate direct child watch directories: %s", root)
        for directory in watch_dirs.values():
            observer.schedule(handler, str(directory), recursive=False)
            logger.info("Watching demo directory: %s", directory)
        observer.start()
        self._observer = observer

    async def stop(self) -> None:
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=5)
        self._observer = None

    async def restart(self, paths: list[str]) -> None:
        self._paths = paths
        await self.stop()
        await self.start()

    async def scan_existing(self) -> dict[str, int]:
        started = time.perf_counter()
        raw_conc = (os.environ.get("CS2_INSIGHT_SCAN_CONCURRENCY") or "").strip()
        try:
            max_conc = int(raw_conc) if raw_conc else 0
        except ValueError:
            max_conc = 0
        if max_conc < 1:
            max_conc = max(2, min(8, (os.cpu_count() or 4)))
        sem = asyncio.Semaphore(max_conc)

        roots = self._normalized_paths()
        dem_paths, zip_paths, existing_paths, visited_dirs, enumeration_errors = await asyncio.to_thread(
            _discover_demo_files_bounded,
            roots,
        )
        processing_errors = 0

        dem_by_key = {_path_key(path): path for path in dem_paths}

        # 先处理 zip；新解压出的路径直接并入本轮结果，不再为每个含 ZIP 的根目录做第二遍扫描。
        for z in zip_paths:
            try:
                zip_result = await self._on_raw_zip_detected(
                    z,
                    enqueue_extracted=False,
                    assume_stable=True,
                )
                processing_errors += zip_result.errors
                for extracted in zip_result.extracted_paths:
                    if not self._path_within_scan_depth(extracted):
                        logger.warning("Ignore extracted demo outside bounded scan scope: %s", extracted)
                        processing_errors += 1
                        continue
                    dem_by_key.setdefault(_path_key(extracted), extracted)
                    existing_paths.add(str(extracted))
            except Exception:
                logger.exception("scan_existing: zip processing failed for %s", z)
                processing_errors += 1

        # 只清理本次扫描根目录内已消失的记录；绝不能影响其它库目录。
        purged_missing = 0
        if self._demo_db is not None and roots and enumeration_errors == 0:
            try:
                purged_missing = await self._demo_db.purge_deleted_demo_files(existing_paths, roots)
            except Exception:
                logger.exception("purge_deleted_demo_files failed during scan")
                processing_errors += 1
        elif enumeration_errors:
            logger.warning(
                "Skip missing-demo purge because bounded directory enumeration had %s error(s)",
                enumeration_errors,
            )

        # purge 完成后再加载索引，避免已删除记录仍被误判为“已知路径”。
        known_keys: set[str] = set()
        blocked_keys: set[str] = set()
        if self._demo_db is not None:
            try:
                known_paths, blocked_paths = await self._demo_db.load_scan_path_index()
                known_keys = {_path_key(path) for path in known_paths}
                blocked_keys = {_path_key(path) for path in blocked_paths}
            except Exception:
                logger.exception("load_scan_path_index failed during scan")
                processing_errors += 1

        new_demos = 0
        skipped_known = 0
        enqueue_errors = 0

        async def _enqueue_dem(path: Path) -> tuple[bool, bool, bool]:
            async with sem:
                key = _path_key(path)
                if key in known_keys or key in blocked_keys:
                    return False, False, False
                try:
                    status = await self._on_detected(path, None)
                    inserted = status is True
                    duplicate = status == "duplicate"
                    if inserted or duplicate:
                        known_keys.add(key)
                    return inserted, False, duplicate
                except Exception:
                    logger.exception("scan_existing: enqueue failed for %s", path)
                    return False, True, False

        all_dems = list(dem_by_key.values())
        if all_dems:
            results = await asyncio.gather(*(_enqueue_dem(item) for item in all_dems))
            new_demos = sum(1 for inserted, _, _ in results if inserted)
            enqueue_errors = sum(1 for _, failed, _ in results if failed)
            skipped_duplicate = sum(1 for _, _, duplicate in results if duplicate)
            skipped_known = len(all_dems) - new_demos - enqueue_errors - skipped_duplicate
        else:
            skipped_duplicate = 0

        elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
        errors = enumeration_errors + processing_errors + enqueue_errors
        return {
            "scanned": len(all_dems) + len(zip_paths),
            "roots_scanned": len(roots),
            "directories_scanned": visited_dirs,
            "demos_found": len(all_dems),
            "archives_found": len(zip_paths),
            "new_demos": new_demos,
            "skipped_known": skipped_known,
            "skipped_duplicate": skipped_duplicate,
            "purged_missing": purged_missing,
            "errors": errors,
            "elapsed_ms": elapsed_ms,
        }
