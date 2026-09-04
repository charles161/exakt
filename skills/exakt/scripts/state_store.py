"""Canonical bytes, trusted state, identities, and the Exakt event journal.

The controller keeps its canonical state outside target repositories.  This
module implements the deliberately small trusted storage layer: strict bytes,
private filesystem policy, stable identities, immutable objects, and an
atomically published hash-chained journal.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import ipaddress
import json
import ntpath
import os
import posixpath
import re
import secrets
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit


def _load_contracts_module():
    """Load only the shipped sibling, never a repository-controlled module."""
    path = Path(__file__).resolve().with_name("contracts.py")
    spec = importlib.util.spec_from_file_location("_exakt_state_contracts", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import Exakt contracts from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return (
        module.ContractError,
        module.MAX_JSON_NESTING_DEPTH,
        module.MAX_JSON_NODES,
        module.MAX_PARSED_INTEGER_DIGITS,
        module._ensure_json_domain,
        module.loads_json_document,
        module.ContractRegistry,
    )


(
    _ContractError,
    MAX_JSON_NESTING_DEPTH,
    MAX_JSON_NODES,
    MAX_INTEGER_DIGITS,
    _ensure_json_domain,
    _loads_json_document,
    _ContractRegistry,
) = _load_contracts_module()

_CONTRACTS = _ContractRegistry()


CANONICAL_JSON_VERSION = "exakt-canonical-json-v1"
REGISTRY_VERSION = "repository-registry-v1"
ID_RANDOM_BYTES = 16
_REPOSITORY_ID_PATTERN = re.compile(r"^repo-[0-9a-f]{32}$")
_WORK_ITEM_ID_PATTERN = re.compile(r"^work-[0-9a-f]{32}$")
_SCOPE_ID_PATTERN = re.compile(r"^scope-[0-9a-f]{64}$")
_GIT_TREE_DIGEST_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SCP_REMOTE_PATTERN = re.compile(
    r"^(?:(?P<user>[^@/:\s]+)@)?(?P<host>[A-Za-z0-9._-]+):(?P<path>[^\s]+)$"
)
_NETWORK_REMOTE_SCHEMES = {"http", "https", "ssh", "git"}
_RUN_STATUSES = frozenset({"active", "suspended", "cancelled"})
_RESUMABLE_RUN_STATUSES = frozenset({"active", "suspended"})
_PROBE_PREFIX = ".exakt-capability-"
MAX_PRIVATE_FILE_BYTES = 64 * 1024 * 1024
MAX_JOURNAL_RECORDS = 100_000
_INT_SERIALIZE_CHUNK_DIGITS = 256
_INT_SERIALIZE_BASE = 10**_INT_SERIALIZE_CHUNK_DIGITS


class StateStoreError(ValueError):
    """Base error for deterministic state-store failures."""


class CanonicalStateError(StateStoreError):
    """Input cannot be represented as Exakt Canonical JSON v1."""


class StateHomeError(StateStoreError):
    """The selected external state home is unsafe or invalid."""


class UnsafeFilesystemError(StateHomeError):
    """The state-home filesystem cannot support trusted resumable state."""


class RepositoryIdentityError(StateStoreError):
    """Repository discovery anchors or identity records are invalid."""


class RepositoryRelocationRequired(RepositoryIdentityError):
    """A path appears rebound and requires explicit live relocation approval."""


class AmbiguousResumeError(StateStoreError):
    """More than one canonical work item matches a resume selector."""


def _validate_utf8_strings(value: Any) -> None:
    """Require every string/key to be valid Unicode without normalization."""
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            try:
                current.encode("utf-8", errors="strict")
            except UnicodeEncodeError as error:
                raise CanonicalStateError(
                    "string contains an invalid Unicode surrogate"
                ) from error
        elif isinstance(current, dict):
            for key, child in current.items():
                if not isinstance(key, str):
                    raise CanonicalStateError("object keys must be strings")
                stack.append(key)
                stack.append(child)
        elif isinstance(current, list):
            stack.extend(current)


def _validate_canonical_domain(value: Any) -> None:
    try:
        _ensure_json_domain(value)
        _validate_utf8_strings(value)
    except CanonicalStateError:
        raise
    except _ContractError as error:
        raise CanonicalStateError(str(error)) from error
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise CanonicalStateError(f"invalid canonical state: {error}") from error


def _integer_to_decimal(value: int) -> str:
    """Render a bounded integer without Python's process-global digit limit."""
    if value == 0:
        return "0"
    negative = value < 0
    remaining = -value if negative else value
    chunks: list[int] = []
    while remaining:
        remaining, chunk = divmod(remaining, _INT_SERIALIZE_BASE)
        chunks.append(chunk)
    leading = str(chunks.pop())
    tail = "".join(
        str(chunk).zfill(_INT_SERIALIZE_CHUNK_DIGITS)
        for chunk in reversed(chunks)
    )
    return ("-" if negative else "") + leading + tail


def _canonical_text(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return _integer_to_decimal(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_canonical_text(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            _canonical_text(key) + ":" + _canonical_text(value[key])
            for key in sorted(value)
        ) + "}"
    raise CanonicalStateError(
        f"value of type {type(value).__name__} is outside the JSON domain"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Encode unframed Exakt Canonical JSON v1 bytes.

    Schema validation is deliberately a caller responsibility.  This function
    validates the bounded JSON domain and byte algorithm only.
    """
    _validate_canonical_domain(value)
    try:
        return _canonical_text(value).encode("utf-8", errors="strict")
    except (UnicodeEncodeError, TypeError, ValueError, OverflowError, RecursionError) as error:
        raise CanonicalStateError(f"cannot encode canonical state: {error}") from error


def canonical_json_record(value: Any) -> bytes:
    """Encode one JSONL record with exactly one LF framing byte."""
    return canonical_json_bytes(value) + b"\n"


def sha256_hex(data: bytes) -> str:
    if not isinstance(data, bytes):
        raise CanonicalStateError("SHA-256 input must be bytes")
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_hex(canonical_json_bytes(value))


def parse_json_bytes(data: bytes, *, require_canonical: bool = False) -> Any:
    """Parse strict UTF-8 JSON, rejecting duplicate keys and non-JSON values."""
    if not isinstance(data, bytes):
        raise CanonicalStateError("JSON input must be bytes")
    if data.startswith(b"\xef\xbb\xbf"):
        raise CanonicalStateError("UTF-8 byte-order marks are forbidden")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CanonicalStateError(f"invalid UTF-8: {error}") from error
    try:
        value = _loads_json_document(text)
    except _ContractError as error:
        raise CanonicalStateError(str(error)) from error
    _validate_canonical_domain(value)
    if require_canonical and canonical_json_bytes(value) != data:
        raise CanonicalStateError("input is not Exakt Canonical JSON v1")
    return value


def _validated_path_text(value: Any, *, label: str) -> str:
    try:
        text = os.fspath(value)
    except TypeError as error:
        raise StateHomeError(f"{label} must be text or path-like") from error
    if not isinstance(text, str) or not text:
        raise StateHomeError(f"{label} must be non-empty text")
    if "\0" in text:
        raise StateHomeError(f"{label} contains NUL")
    try:
        text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise StateHomeError(f"{label} is not valid UTF-8") from error
    return text


def _absolute_configured_path(raw: str, *, variable: str, home: Path) -> Path:
    raw = _validated_path_text(raw, label=variable)
    if raw == "~" or raw.startswith(("~/", "~\\")):
        raw = str(home) + raw[1:]
    path = Path(raw)
    if not path.is_absolute():
        raise StateHomeError(f"{variable} must be an absolute path")
    return Path(os.path.abspath(path))


def resolve_state_home(
    environ: Mapping[str, str] | None = None,
    *,
    platform_name: str | None = None,
    home: str | Path | None = None,
) -> Path:
    """Resolve state-home precedence without creating or resolving the path."""
    env = os.environ if environ is None else environ
    platform_value = sys.platform if platform_name is None else platform_name
    if not isinstance(env, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in env.items()
    ):
        raise StateHomeError("environment must map text keys to text values")
    if not isinstance(platform_value, str):
        raise StateHomeError("platform name must be text")
    try:
        selected_home = Path.home() if home is None else home
        home_path = Path(_validated_path_text(selected_home, label="home directory"))
    except (StateHomeError, RuntimeError, TypeError, ValueError) as error:
        raise StateHomeError("home directory path is invalid") from error
    if not home_path.is_absolute():
        raise StateHomeError("home directory must be absolute")

    if "EXAKT_STATE_HOME" in env:
        return _absolute_configured_path(
            env["EXAKT_STATE_HOME"], variable="EXAKT_STATE_HOME", home=home_path
        )

    is_windows = platform_value.startswith("win")
    if not is_windows and "XDG_STATE_HOME" in env:
        xdg = _absolute_configured_path(
            env["XDG_STATE_HOME"], variable="XDG_STATE_HOME", home=home_path
        )
        return xdg / "exakt"
    if platform_value == "darwin":
        return home_path / "Library/Application Support/Exakt"
    if is_windows:
        if "LOCALAPPDATA" not in env:
            raise StateHomeError("LOCALAPPDATA is required on Windows")
        local = _absolute_configured_path(
            env["LOCALAPPDATA"], variable="LOCALAPPDATA", home=home_path
        )
        return local / "Exakt"
    return home_path / ".local/state/exakt"


def _normalized_path_parts(
    value: str | os.PathLike[str],
    *,
    flavor: str,
    case_sensitive: bool,
) -> tuple[str, ...]:
    text = _validated_path_text(value, label="path")
    if flavor == "windows":
        normalized = ntpath.normpath(text)
        path = PureWindowsPath(normalized)
    elif flavor == "posix":
        normalized = posixpath.normpath(text)
        path = PurePosixPath(normalized)
    else:
        raise StateHomeError(f"unknown path flavor: {flavor}")
    parts = tuple(path.parts)
    if not case_sensitive:
        parts = tuple(part.casefold() for part in parts)
    return parts


def paths_overlap(
    first: str | os.PathLike[str],
    second: str | os.PathLike[str],
    *,
    flavor: str = "native",
    case_sensitive: bool | None = None,
) -> bool:
    """Return whether either normalized path is equal to/inside the other."""
    if case_sensitive is not None and not isinstance(case_sensitive, bool):
        raise StateHomeError("case sensitivity policy must be boolean or None")
    selected_flavor = (
        "windows" if os.name == "nt" else "posix"
    ) if flavor == "native" else flavor
    sensitive = (
        selected_flavor != "windows" if case_sensitive is None else case_sensitive
    )
    left = _normalized_path_parts(first, flavor=selected_flavor, case_sensitive=sensitive)
    right = _normalized_path_parts(second, flavor=selected_flavor, case_sensitive=sensitive)
    if not left or not right:
        return False
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return longer[: len(shorter)] == shorter


def _resolved_path(path: str | os.PathLike[str], *, label: str) -> Path:
    text = _validated_path_text(path, label=label)
    candidate = Path(text)
    if not candidate.is_absolute():
        raise StateHomeError(f"{label} must be absolute")
    if candidate.is_symlink() and not candidate.exists():
        raise StateHomeError(f"{label} is a broken symlink")
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError, UnicodeError, ValueError) as error:
        raise StateHomeError(f"cannot resolve {label}: {error}") from error


def _iter_target_links(target: Path, *, max_entries: int = MAX_JSON_NODES):
    if (
        not isinstance(max_entries, int)
        or isinstance(max_entries, bool)
        or max_entries < 1
    ):
        raise StateHomeError("target link scan limit must be a positive integer")
    visited = 0
    pending = [target]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > max_entries:
                        raise StateHomeError(
                            "target link scan exceeds portable limit of "
                            f"{max_entries} entries"
                        )
                    path = Path(entry.path)
                    if entry.is_symlink():
                        yield path
                    elif entry.is_dir(follow_symlinks=False):
                        pending.append(path)
        except StateHomeError:
            raise
        except OSError as error:
            raise StateHomeError(
                f"cannot inspect target-owned links: {error}"
            ) from error


def assert_safe_state_home_path(
    state_home: str | os.PathLike[str],
    target_roots: Iterable[str | os.PathLike[str]],
    *,
    case_sensitive: bool | None = None,
) -> Path:
    """Resolve and reject every direct or target-link state/target overlap."""
    resolved_state = _resolved_path(state_home, label="state home")
    if isinstance(target_roots, (str, bytes)):
        raise StateHomeError("target roots must be an iterable of paths")
    try:
        targets = list(target_roots)
    except TypeError as error:
        raise StateHomeError("target roots must be an iterable of paths") from error
    resolved_targets: list[Path] = []
    for target_root in targets:
        target = _resolved_path(target_root, label="target root")
        if not target.is_dir():
            raise StateHomeError(f"target root is not a directory: {target}")
        if paths_overlap(
            resolved_state,
            target,
            case_sensitive=case_sensitive,
        ):
            raise StateHomeError(
                f"state home overlaps target root: {resolved_state} and {target}"
            )
        resolved_targets.append(target)

    for target in resolved_targets:
        for link in _iter_target_links(target):
            if not link.exists():
                raise StateHomeError(f"broken symlink under target root: {link}")
            try:
                destination = link.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise StateHomeError(
                    f"cannot resolve target-owned symlink {link}: {error}"
                ) from error
            if paths_overlap(
                resolved_state,
                destination,
                case_sensitive=case_sensitive,
            ):
                raise StateHomeError(
                    f"target-owned symlink reaches state home: {link}"
                )
    return resolved_state


def _private_mode(path: Path, expected: int) -> bool:
    details = path.stat(follow_symlinks=False)
    return stat.S_IMODE(details.st_mode) == expected


def _private_acl_is_verifiable_and_restrictive(path_or_fd: Path | int) -> bool:
    """Conservatively accept only a basic POSIX mode ACL on this adapter."""
    if os.name != "posix" or not sys.platform.startswith("linux"):
        return False
    if not hasattr(os, "listxattr"):
        return False
    try:
        attributes = (
            os.listxattr(path_or_fd)
            if isinstance(path_or_fd, int)
            else os.listxattr(path_or_fd, follow_symlinks=False)
        )
    except OSError:
        return False
    # A stored POSIX ACL may include named principals not represented by mode
    # bits.  V1's stdlib adapter cannot safely interpret it, so fail closed.
    return "system.posix_acl_access" not in attributes


def _assert_private_directory(path: Path) -> None:
    try:
        details = path.stat(follow_symlinks=False)
    except OSError as error:
        raise StateHomeError(f"cannot inspect state home: {error}") from error
    if not stat.S_ISDIR(details.st_mode):
        raise StateHomeError("state home must be a private directory")
    if hasattr(os, "getuid") and details.st_uid != os.getuid():
        raise StateHomeError("state home is not owned by the effective user")
    if stat.S_IMODE(details.st_mode) != 0o700:
        raise StateHomeError("state home permissions are not private (required: 0700)")
    if not _private_acl_is_verifiable_and_restrictive(path):
        raise StateHomeError("state home ACL privacy cannot be verified")


def _create_private_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, mode=0o700, exist_ok=False)
    except FileExistsError:
        _assert_private_directory(path)
        return
    except OSError as error:
        raise StateHomeError(f"cannot create state home: {error}") from error
    try:
        path.chmod(0o700)
        _assert_private_directory(path)
    except Exception:
        with contextlib.suppress(OSError):
            path.rmdir()
        raise


@dataclass(frozen=True)
class FilesystemCapabilities:
    private_permissions: bool
    symlink_safe_creation: bool
    exclusive_locking: bool
    atomic_replace: bool
    file_sync: bool
    directory_sync: bool
    compare_and_swap: bool

    @property
    def trusted(self) -> bool:
        return all(
            (
                self.private_permissions,
                self.symlink_safe_creation,
                self.exclusive_locking,
                self.atomic_replace,
                self.file_sync,
                self.directory_sync,
                self.compare_and_swap,
            )
        )

    def failed_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in (
                "private_permissions",
                "symlink_safe_creation",
                "exclusive_locking",
                "atomic_replace",
                "file_sync",
                "directory_sync",
                "compare_and_swap",
            )
            if not getattr(self, name)
        )


@dataclass(frozen=True)
class PreparedStateHome:
    path: Path
    capabilities: FilesystemCapabilities


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("short write while probing state filesystem")
        offset += written


def _sync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _exclusive_open(
    path: Path, mode: int = 0o600, *, cleanup_on_failure: bool = False
) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    else:
        raise OSError("O_NOFOLLOW is unavailable")
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, flags, mode)
        created = True
        os.fchmod(descriptor, mode)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise UnsafeFilesystemError(f"private file is not regular: {path.name}")
        if hasattr(os, "getuid") and details.st_uid != os.getuid():
            raise UnsafeFilesystemError(f"private file has the wrong owner: {path.name}")
        if stat.S_IMODE(details.st_mode) != mode:
            raise UnsafeFilesystemError(
                f"private file mode must be {mode:04o}: {path.name}"
            )
        if not _private_acl_is_verifiable_and_restrictive(descriptor):
            raise UnsafeFilesystemError(
                f"private file ACL privacy cannot be verified: {path.name}"
            )
        return descriptor
    except Exception:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        # A published lock name must remain in place even when setup fails;
        # unlinking it would let another process lock a different inode.
        # Unique temporary files are safe to clean because nobody locks them.
        if created and cleanup_on_failure:
            with contextlib.suppress(OSError):
                path.unlink()
        raise


def _open_private_existing_file(path: Path, flags: int) -> int:
    if path.is_symlink():
        raise UnsafeFilesystemError(f"refusing symlinked private file: {path.name}")
    try:
        before = path.stat(follow_symlinks=False)
    except OSError:
        raise
    if not stat.S_ISREG(before.st_mode):
        raise UnsafeFilesystemError(f"private file is not regular: {path.name}")
    open_flags = flags
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    else:
        raise UnsafeFilesystemError("O_NOFOLLOW is unavailable")
    if hasattr(os, "O_NONBLOCK"):
        # Prevent a type-swap to a FIFO/device between lstat and open from
        # blocking the trusted controller. Regular-file semantics are unchanged.
        open_flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, open_flags)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise UnsafeFilesystemError(f"private file is not regular: {path.name}")
        if hasattr(os, "getuid") and details.st_uid != os.getuid():
            raise UnsafeFilesystemError(f"private file has the wrong owner: {path.name}")
        if stat.S_IMODE(details.st_mode) != 0o600:
            raise UnsafeFilesystemError(f"private file mode must be 0600: {path.name}")
        if not _private_acl_is_verifiable_and_restrictive(descriptor):
            raise UnsafeFilesystemError(
                f"private file ACL privacy cannot be verified: {path.name}"
            )
        return descriptor
    except Exception:
        if "descriptor" in locals():
            with contextlib.suppress(OSError):
                os.close(descriptor)
        raise


def _probe_cross_process_lock(path: Path) -> bool:
    if os.name != "posix":
        return False
    import fcntl

    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        program = (
            "import fcntl, os, sys\n"
            "fd = os.open(sys.argv[1], os.O_RDWR | os.O_NOFOLLOW)\n"
            "try:\n"
            "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "except BlockingIOError:\n"
            "    os.close(fd); raise SystemExit(0)\n"
            "else:\n"
            "    fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd); raise SystemExit(2)\n"
        )
        result = subprocess.run(
            (sys.executable, "-c", program, str(path)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(descriptor)


def probe_state_home_filesystem(path: str | os.PathLike[str]) -> FilesystemCapabilities:
    """Exercise required semantics on the actual selected filesystem.

    This release implements the locking probe on POSIX.  Other hosts fail the
    capability gate honestly until a separately tested adapter is supplied.
    """
    state_home = Path(_validated_path_text(path, label="state home"))
    _assert_private_directory(state_home)
    probe_root = state_home / f"{_PROBE_PREFIX}{secrets.token_hex(8)}"
    flags = {
        "private_permissions": False,
        "symlink_safe_creation": False,
        "exclusive_locking": False,
        "atomic_replace": False,
        "file_sync": False,
        "directory_sync": False,
        "compare_and_swap": False,
    }
    cleanup_error: OSError | None = None
    probe_root_created = False
    try:
        os.mkdir(probe_root, 0o700)
        probe_root_created = True
        os.chmod(probe_root, 0o700)
        flags["private_permissions"] = (
            _private_mode(probe_root, 0o700)
            and _private_acl_is_verifiable_and_restrictive(probe_root)
        )

        victim = probe_root / "victim"
        victim.write_bytes(b"unchanged")
        victim.chmod(0o600)
        link = probe_root / "exclusive-link"
        os.symlink(victim.name, link)
        try:
            descriptor = _exclusive_open(link)
        except FileExistsError:
            flags["symlink_safe_creation"] = victim.read_bytes() == b"unchanged"
        else:
            os.close(descriptor)

        sync_file = probe_root / "file-sync"
        descriptor = _exclusive_open(sync_file)
        try:
            _write_all(descriptor, b"synced")
            os.fsync(descriptor)
            flags["file_sync"] = True
        finally:
            os.close(descriptor)

        source = probe_root / "replace-source"
        destination = probe_root / "replace-destination"
        source.write_bytes(b"new")
        destination.write_bytes(b"old")
        os.replace(source, destination)
        flags["atomic_replace"] = destination.read_bytes() == b"new" and not source.exists()

        _sync_directory(probe_root)
        flags["directory_sync"] = True

        lock_path = probe_root / "lock"
        flags["exclusive_locking"] = _probe_cross_process_lock(lock_path)

        head = probe_root / "cas-head"
        head.write_bytes(b"root-a")
        head.chmod(0o600)
        expected = sha256_hex(b"root-a")
        replaced = compare_and_swap_file(head, expected, b"root-b")
        stale_rejected = not compare_and_swap_file(head, expected, b"root-c")
        flags["compare_and_swap"] = (
            replaced
            and stale_rejected
            and head.read_bytes() == b"root-b"
            and flags["exclusive_locking"]
            and flags["atomic_replace"]
            and flags["file_sync"]
            and flags["directory_sync"]
        )
    except (OSError, ValueError):
        # Individual flags remain false; the caller reports the exact gate set.
        pass
    finally:
        try:
            if probe_root_created:
                if probe_root.is_symlink():
                    raise OSError("probe directory was replaced by a symlink")
                if probe_root.exists():
                    shutil.rmtree(probe_root)
            _sync_directory(state_home)
        except OSError as error:
            cleanup_error = error
    if cleanup_error is not None:
        raise UnsafeFilesystemError(
            f"filesystem capability probe cleanup failed: {cleanup_error}"
        ) from cleanup_error
    return FilesystemCapabilities(**flags)


def prepare_state_home(
    state_home: str | os.PathLike[str],
    target_roots: Iterable[str | os.PathLike[str]],
    *,
    case_sensitive: bool | None = None,
    capability_probe: Callable[[Path], FilesystemCapabilities] | None = None,
) -> PreparedStateHome:
    safe_path = assert_safe_state_home_path(
        state_home, target_roots, case_sensitive=case_sensitive
    )
    _create_private_directory(safe_path)
    _assert_private_directory(safe_path)
    probe = probe_state_home_filesystem if capability_probe is None else capability_probe
    capabilities = probe(safe_path)
    if not isinstance(capabilities, FilesystemCapabilities):
        raise UnsafeFilesystemError("capability probe returned an invalid result")
    if not capabilities.trusted:
        failed = ", ".join(capabilities.failed_names())
        raise UnsafeFilesystemError(f"unsafe state-home filesystem: {failed}")
    return PreparedStateHome(path=safe_path, capabilities=capabilities)


def _normalize_remote_host(host: str) -> str:
    if not isinstance(host, str) or not host or "%" in host:
        raise RepositoryIdentityError("remote hostname contains invalid escaping")
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        raise RepositoryIdentityError("remote hostname is invalid") from None
    if ":" in ascii_host:
        try:
            return str(ipaddress.IPv6Address(ascii_host))
        except ipaddress.AddressValueError:
            raise RepositoryIdentityError("remote hostname is invalid") from None
    labels = ascii_host.split(".")
    if any(
        not label
        or len(label) > 63
        or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
        for label in labels
    ):
        raise RepositoryIdentityError("remote hostname is invalid")
    return ascii_host


def _validate_remote_path(path: str) -> None:
    if not isinstance(path, str) or not path or "\0" in path or "\\" in path:
        raise RepositoryIdentityError("remote URL has an invalid repository path")
    try:
        path.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise RepositoryIdentityError("remote repository path is not valid UTF-8") from None


def sanitize_remote_url(remote: str) -> str:
    """Return a credential/query/fragment-free discovery anchor."""
    if not isinstance(remote, str) or not remote or remote != remote.strip():
        raise RepositoryIdentityError("remote URL must be non-empty trimmed text")
    if any(character.isspace() or ord(character) < 32 for character in remote):
        raise RepositoryIdentityError("remote URL contains whitespace or controls")
    windows_drive, _tail = ntpath.splitdrive(remote)
    if windows_drive:
        raise RepositoryIdentityError("local Windows paths are not remote URLs")

    scp_match = _SCP_REMOTE_PATTERN.fullmatch(remote)
    if scp_match and "://" not in remote:
        host = _normalize_remote_host(scp_match.group("host"))
        path = scp_match.group("path").lstrip("/")
        if (
            not host
            or not path
            or path.startswith("../")
            or "?" in path
            or "#" in path
        ):
            raise RepositoryIdentityError("malformed SCP-style remote")
        _validate_remote_path(path)
        return f"ssh://{host}/{path}"

    try:
        parsed = urlsplit(remote)
        port = parsed.port
    except ValueError:
        raise RepositoryIdentityError("malformed remote URL") from None
    scheme = parsed.scheme.lower()
    if scheme not in _NETWORK_REMOTE_SCHEMES or not parsed.hostname:
        raise RepositoryIdentityError("only network VCS remote URLs are permitted")
    host = _normalize_remote_host(parsed.hostname)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host + (f":{port}" if port is not None else "")
    path = parsed.path
    if not path or not path.startswith("/") or "\0" in path:
        raise RepositoryIdentityError("remote URL must contain an absolute repository path")
    _validate_remote_path(path)
    return f"{scheme}://{netloc}{path}"


def sanitize_remote_urls(remotes: Iterable[str]) -> tuple[str, ...]:
    try:
        values = list(remotes)
    except TypeError as error:
        raise RepositoryIdentityError("remote URLs must be an iterable of strings") from error
    sanitized = {sanitize_remote_url(remote) for remote in values}
    return tuple(sorted(sanitized))


def _new_random_id(
    prefix: str,
    *,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> str:
    raw = random_bytes(ID_RANDOM_BYTES)
    if not isinstance(raw, bytes) or len(raw) != ID_RANDOM_BYTES:
        raise StateStoreError("random source must return exactly 16 bytes")
    return f"{prefix}-{raw.hex()}"


def new_repository_id(
    *, random_bytes: Callable[[int], bytes] = secrets.token_bytes
) -> str:
    return _new_random_id("repo", random_bytes=random_bytes)


def new_work_item_id(
    *, random_bytes: Callable[[int], bytes] = secrets.token_bytes
) -> str:
    return _new_random_id("work", random_bytes=random_bytes)


def allocate_unique_work_item_id(
    existing_ids: Iterable[str],
    *,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> str:
    try:
        values = list(existing_ids)
    except TypeError as error:
        raise StateStoreError("existing work-item IDs must be iterable") from error
    if any(
        not isinstance(value, str)
        or _WORK_ITEM_ID_PATTERN.fullmatch(value) is None
        for value in values
    ):
        raise StateStoreError("existing work-item IDs contain an invalid value")
    existing = set(values)
    for _ in range(32):
        candidate = new_work_item_id(random_bytes=random_bytes)
        if candidate not in existing:
            return candidate
    raise StateStoreError("random work-item ID source repeatedly collided")


def scope_id_for_repositories(repository_ids: Iterable[str]) -> str:
    try:
        values = list(repository_ids)
    except TypeError as error:
        raise RepositoryIdentityError("repository IDs must be iterable") from error
    if not values:
        raise RepositoryIdentityError("at least one repository ID is required")
    if any(
        not isinstance(value, str)
        or _REPOSITORY_ID_PATTERN.fullmatch(value) is None
        for value in values
    ):
        raise RepositoryIdentityError("scope contains an invalid repository ID")
    if len(set(values)) != len(values):
        raise RepositoryIdentityError("repository IDs in a scope must be unique")
    ordered = sorted(values)
    if len(ordered) == 1:
        preimage = b"exakt-scope-v1\n" + ordered[0].encode("ascii")
    else:
        preimage = b"exakt-target-set-v1\n" + b"\n".join(
            value.encode("ascii") for value in ordered
        )
    return "scope-" + sha256_hex(preimage)


def assert_no_case_collisions(
    paths: Iterable[str], *, case_sensitive: bool
) -> None:
    if not isinstance(case_sensitive, bool):
        raise RepositoryIdentityError("case sensitivity policy must be boolean")
    try:
        values = list(paths)
    except TypeError as error:
        raise RepositoryIdentityError("repository paths must be iterable") from error
    seen: dict[str, str] = {}
    for raw in values:
        if (
            not isinstance(raw, str)
            or not raw
            or "\0" in raw
            or "\\" in raw
            or raw.startswith("/")
        ):
            raise RepositoryIdentityError("repository paths must be relative UTF-8 text")
        try:
            raw.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise RepositoryIdentityError("repository path is not valid UTF-8") from error
        normalized = posixpath.normpath(raw)
        if (
            normalized != raw
            or normalized in {".", ".."}
            or normalized.startswith("../")
            or any(part in {"", ".", ".."} for part in raw.split("/"))
        ):
            raise RepositoryIdentityError(
                "repository paths must be normalized root-relative POSIX paths"
            )
        if case_sensitive:
            continue
        folded = normalized.casefold()
        prior = seen.get(folded)
        if prior is not None and prior != normalized:
            raise RepositoryIdentityError(
                f"case-colliding repository entries: {prior!r} and {normalized!r}"
            )
        seen[folded] = normalized


def _validate_repository_id(value: str) -> None:
    if not isinstance(value, str) or _REPOSITORY_ID_PATTERN.fullmatch(value) is None:
        raise RepositoryIdentityError("invalid repository ID")


def _validate_tree_digest(vcs_kind: str, tree_digest: Any) -> None:
    pattern = _GIT_TREE_DIGEST_PATTERN if vcs_kind == "git" else _SHA256_DIGEST_PATTERN
    if not isinstance(tree_digest, str) or pattern.fullmatch(tree_digest) is None:
        kind = "Git tree OID" if vcs_kind == "git" else "Exakt SHA-256 content anchor"
        raise RepositoryIdentityError(f"initial tree digest must be a valid {kind}")


def _validate_resolved_root(root: Any) -> None:
    if not isinstance(root, str) or not root or "\0" in root or "\\" in root:
        raise RepositoryIdentityError("repository record has an invalid resolved root")
    try:
        root.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise RepositoryIdentityError("repository record root is not valid UTF-8") from error

    if PurePosixPath(root).is_absolute():
        normalized = posixpath.normpath(root)
    elif PureWindowsPath(root).is_absolute():
        normalized = ntpath.normpath(root).replace("\\", "/")
    else:
        raise RepositoryIdentityError("repository record has an invalid resolved root")
    if normalized != root or any(part in {".", ".."} for part in root.split("/")):
        raise RepositoryIdentityError(
            "repository record root must use canonical resolved forward-slash form"
        )


def _validate_repository_record(record: Any) -> None:
    expected = {
        "repository_id",
        "resolved_root",
        "vcs_kind",
        "remote_urls",
        "filesystem_identity",
        "initial_tree_digest",
    }
    if not isinstance(record, dict) or set(record) != expected:
        raise RepositoryIdentityError("malformed repository record")
    _validate_repository_id(record["repository_id"])
    root = record["resolved_root"]
    _validate_resolved_root(root)
    vcs_kind = record["vcs_kind"]
    if not isinstance(vcs_kind, str) or re.fullmatch(
        r"[a-z][a-z0-9_-]*", vcs_kind
    ) is None:
        raise RepositoryIdentityError("repository record has an invalid VCS kind")
    remotes = record["remote_urls"]
    if not isinstance(remotes, list) or any(
        not isinstance(remote, str) for remote in remotes
    ):
        raise RepositoryIdentityError("repository record has invalid remote URLs")
    try:
        normalized_remotes = list(sanitize_remote_urls(remotes))
    except RepositoryIdentityError:
        raise
    if remotes != normalized_remotes:
        raise RepositoryIdentityError(
            "repository record remote URLs are not sanitized, sorted, and unique"
        )
    filesystem = record["filesystem_identity"]
    if not isinstance(filesystem, dict) or set(filesystem) != {"device", "inode"}:
        raise RepositoryIdentityError("repository record has invalid filesystem identity")
    if any(
        not isinstance(filesystem[name], int)
        or isinstance(filesystem[name], bool)
        or filesystem[name] < 0
        for name in ("device", "inode")
    ):
        raise RepositoryIdentityError("repository filesystem identity must use integers")
    _validate_tree_digest(vcs_kind, record["initial_tree_digest"])


def make_repository_record(
    root: str | os.PathLike[str],
    *,
    vcs_kind: str,
    remote_urls: Iterable[str],
    initial_tree_digest: str,
    repository_id: str | None = None,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> dict[str, Any]:
    try:
        root_text = _validated_path_text(root, label="repository root")
        resolved_root = Path(root_text).resolve(strict=True)
    except (StateHomeError, OSError, RuntimeError, UnicodeError, ValueError) as error:
        raise RepositoryIdentityError(f"cannot resolve repository root: {error}") from error
    if not resolved_root.is_dir():
        raise RepositoryIdentityError("repository root must be a directory")
    try:
        normalized_root = resolved_root.as_posix()
        normalized_root.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise RepositoryIdentityError("repository root is not valid UTF-8") from error
    if not isinstance(vcs_kind, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", vcs_kind):
        raise RepositoryIdentityError("invalid VCS kind")
    _validate_tree_digest(vcs_kind, initial_tree_digest)
    identity = (
        new_repository_id(random_bytes=random_bytes)
        if repository_id is None
        else repository_id
    )
    _validate_repository_id(identity)
    details = resolved_root.stat()
    record: dict[str, Any] = {
        "repository_id": identity,
        "resolved_root": normalized_root,
        "vcs_kind": vcs_kind,
        "remote_urls": list(sanitize_remote_urls(remote_urls)),
        "filesystem_identity": {
            "device": int(details.st_dev),
            "inode": int(details.st_ino),
        },
        "initial_tree_digest": initial_tree_digest,
    }
    canonical_json_bytes(record)
    _validate_repository_record(record)
    return record


@contextlib.contextmanager
def _registry_lock(path: Path):
    if os.name != "posix":
        raise UnsafeFilesystemError(
            "repository registry locking is unverified on this platform"
        )
    import fcntl

    descriptor: int | None = None
    try:
        try:
            descriptor = _exclusive_open(path, 0o600)
            os.fchmod(descriptor, 0o600)
        except FileExistsError:
            descriptor = _open_private_existing_file(path, os.O_RDWR)
    except OSError as error:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        raise UnsafeFilesystemError(
            f"cannot open repository registry lock safely: {error}"
        ) from error
    if descriptor is None:
        raise UnsafeFilesystemError("repository registry lock did not open")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError as error:
        os.close(descriptor)
        raise UnsafeFilesystemError(
            f"repository registry lock operation failed: {error}"
        ) from error
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _write_private_file_atomic(
    path: Path,
    payload: bytes,
    *,
    before_replace: Callable[[], None] | None = None,
    after_replace: Callable[[], None] | None = None,
) -> None:
    if not isinstance(payload, bytes):
        raise UnsafeFilesystemError("private state payload must be bytes")
    if len(payload) > MAX_PRIVATE_FILE_BYTES:
        raise UnsafeFilesystemError(
            f"private state payload exceeds {MAX_PRIVATE_FILE_BYTES} bytes"
        )
    _assert_private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    descriptor: int | None = None
    temporary_created = False
    try:
        descriptor = _exclusive_open(temporary, 0o600, cleanup_on_failure=True)
        temporary_created = True
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if _read_private_file_bytes(temporary) != payload:
            raise UnsafeFilesystemError(
                "private state temporary verification failed"
            )
        if before_replace is not None:
            before_replace()
        os.replace(temporary, path)
        if after_replace is not None:
            after_replace()
        _sync_directory(path.parent)
    except OSError as error:
        raise UnsafeFilesystemError(f"cannot persist private state file: {error}") from error
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        if temporary_created:
            with contextlib.suppress(OSError):
                if temporary.is_symlink() or temporary.exists():
                    temporary.unlink()


def _read_private_file_bytes(
    path: Path, *, max_bytes: int = MAX_PRIVATE_FILE_BYTES
) -> bytes:
    descriptor = _open_private_existing_file(path, os.O_RDONLY)
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise UnsafeFilesystemError(
                    f"private state file exceeds {max_bytes} bytes: {path.name}"
                )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def compare_and_swap_file(
    path: str | os.PathLike[str],
    expected_digest: str,
    replacement: bytes,
    *,
    lock_path: str | os.PathLike[str] | None = None,
) -> bool:
    """Atomically replace one private file iff its locked digest still matches."""
    try:
        target = Path(_validated_path_text(path, label="compare-and-swap target"))
    except (StateHomeError, TypeError, ValueError) as error:
        raise UnsafeFilesystemError("compare-and-swap target is invalid") from error
    if not target.is_absolute():
        raise UnsafeFilesystemError("compare-and-swap target must be absolute")
    if (
        not isinstance(expected_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None
    ):
        raise UnsafeFilesystemError("expected compare-and-swap digest is invalid")
    if not isinstance(replacement, bytes):
        raise UnsafeFilesystemError("compare-and-swap replacement must be bytes")
    if len(replacement) > MAX_PRIVATE_FILE_BYTES:
        raise UnsafeFilesystemError(
            f"compare-and-swap replacement exceeds {MAX_PRIVATE_FILE_BYTES} bytes"
        )
    _assert_private_directory(target.parent)
    try:
        selected_lock = (
            Path(_validated_path_text(lock_path, label="compare-and-swap lock"))
            if lock_path is not None
            else target.with_name(f".{target.name}.cas.lock")
        )
    except (StateHomeError, TypeError, ValueError) as error:
        raise UnsafeFilesystemError("compare-and-swap lock path is invalid") from error
    if selected_lock == target:
        raise UnsafeFilesystemError("compare-and-swap lock cannot be the target")
    if not selected_lock.is_absolute() or selected_lock.parent != target.parent:
        raise UnsafeFilesystemError("compare-and-swap lock must be a sibling")
    with _registry_lock(selected_lock):
        current = _read_private_file_bytes(target)
        if sha256_hex(current) != expected_digest:
            return False
        _write_private_file_atomic(target, replacement)
        installed = _read_private_file_bytes(target)
        if installed != replacement:
            raise UnsafeFilesystemError("compare-and-swap replacement verification failed")
        return True


class RepositoryRegistry:
    """Small external registry that assigns identity only by exact root binding."""

    def __init__(self, state_home: str | os.PathLike[str]):
        try:
            state_text = _validated_path_text(state_home, label="state home")
            self.state_home = Path(state_text).resolve(strict=True)
        except (StateHomeError, OSError, RuntimeError, UnicodeError, ValueError) as error:
            raise StateHomeError(f"cannot resolve repository state home: {error}") from error
        _assert_private_directory(self.state_home)
        self.path = self.state_home / "repositories-v1.json"
        self.lock_path = self.state_home / ".repositories-v1.lock"

    def _load(self) -> dict[str, Any]:
        if self.path.is_symlink():
            raise UnsafeFilesystemError("refusing symlinked repository registry")
        if not self.path.exists():
            return {"schema_version": REGISTRY_VERSION, "repositories": []}
        try:
            payload = _read_private_file_bytes(self.path)
        except OSError as error:
            raise RepositoryIdentityError(f"cannot read repository registry: {error}") from error
        document = parse_json_bytes(payload, require_canonical=True)
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "repositories",
        }:
            raise RepositoryIdentityError("malformed repository registry shape")
        if document["schema_version"] != REGISTRY_VERSION or not isinstance(
            document["repositories"], list
        ):
            raise RepositoryIdentityError("unknown or malformed repository registry")
        identifiers: set[str] = set()
        roots: set[str] = set()
        filesystem_identities: set[tuple[int, int]] = set()
        for record in document["repositories"]:
            _validate_repository_record(record)
            if record["repository_id"] in identifiers:
                raise RepositoryIdentityError("duplicate repository ID in registry")
            if record["resolved_root"] in roots:
                raise RepositoryIdentityError("duplicate repository root in registry")
            filesystem = record["filesystem_identity"]
            filesystem_identity = (filesystem["device"], filesystem["inode"])
            if filesystem_identity in filesystem_identities:
                raise RepositoryIdentityError(
                    "duplicate repository filesystem identity in registry"
                )
            identifiers.add(record["repository_id"])
            roots.add(record["resolved_root"])
            filesystem_identities.add(filesystem_identity)
        return document

    def _save(self, document: dict[str, Any]) -> None:
        document["repositories"] = sorted(
            document["repositories"], key=lambda item: item["repository_id"]
        )
        _write_private_file_atomic(self.path, canonical_json_bytes(document))

    def get_or_create(
        self,
        root: str | os.PathLike[str],
        *,
        vcs_kind: str,
        remote_urls: Iterable[str],
        initial_tree_digest: str,
        random_bytes: Callable[[int], bytes] = secrets.token_bytes,
    ) -> dict[str, Any]:
        candidate = make_repository_record(
            root,
            vcs_kind=vcs_kind,
            remote_urls=remote_urls,
            initial_tree_digest=initial_tree_digest,
            repository_id="repo-" + ("0" * 32),
        )
        with _registry_lock(self.lock_path):
            document = self._load()
            for existing in document["repositories"]:
                if existing["resolved_root"] != candidate["resolved_root"]:
                    continue
                if existing["filesystem_identity"] != candidate["filesystem_identity"]:
                    raise RepositoryRelocationRequired(
                        "repository path now refers to a different filesystem identity"
                    )
                return dict(existing)

            for existing in document["repositories"]:
                if existing["filesystem_identity"] == candidate["filesystem_identity"]:
                    raise RepositoryRelocationRequired(
                        "repository filesystem identity moved to a different root; "
                        "explicit relocation approval is required"
                    )

            existing_ids = {
                record["repository_id"] for record in document["repositories"]
            }
            identity = None
            for _ in range(32):
                proposed = new_repository_id(random_bytes=random_bytes)
                if proposed not in existing_ids:
                    identity = proposed
                    break
            if identity is None:
                raise RepositoryIdentityError(
                    "random repository ID source repeatedly collided"
                )
            candidate["repository_id"] = identity
            document["repositories"].append(candidate)
            self._save(document)
            return dict(candidate)


def select_resume_candidate(
    candidates: Sequence[Mapping[str, Any]],
    *,
    scope_id: str,
    work_item_id: str | None = None,
    title: str | None = None,
) -> Mapping[str, Any] | None:
    if not isinstance(scope_id, str) or _SCOPE_ID_PATTERN.fullmatch(scope_id) is None:
        raise StateStoreError("invalid scope ID")
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise StateStoreError("resume candidates must be a sequence")
    if work_item_id is not None and (
        not isinstance(work_item_id, str)
        or _WORK_ITEM_ID_PATTERN.fullmatch(work_item_id) is None
    ):
        raise StateStoreError("invalid work-item ID")
    if title is not None and (not isinstance(title, str) or not title):
        raise StateStoreError("resume title must be non-empty text")
    normalized: list[Mapping[str, Any]] = []
    for item in candidates:
        if not isinstance(item, Mapping):
            raise StateStoreError("resume candidate must be an object")
        candidate_scope = item.get("scope_id")
        candidate_id = item.get("work_item_id")
        candidate_title = item.get("title")
        candidate_status = item.get("status")
        if (
            not isinstance(candidate_scope, str)
            or _SCOPE_ID_PATTERN.fullmatch(candidate_scope) is None
            or not isinstance(candidate_id, str)
            or _WORK_ITEM_ID_PATTERN.fullmatch(candidate_id) is None
            or not isinstance(candidate_title, str)
            or not candidate_title
            or not isinstance(candidate_status, str)
            or candidate_status not in _RUN_STATUSES
        ):
            raise StateStoreError("malformed resume candidate")
        if candidate_status in _RESUMABLE_RUN_STATUSES:
            normalized.append(item)
    scoped = [item for item in normalized if item["scope_id"] == scope_id]
    if work_item_id is not None:
        matches = [item for item in scoped if item.get("work_item_id") == work_item_id]
    elif title is not None:
        matches = [item for item in scoped if item.get("title") == title]
    else:
        matches = list(scoped)
    if not matches:
        return None
    if len(matches) > 1:
        identifiers = sorted(str(item.get("work_item_id")) for item in matches)
        raise AmbiguousResumeError(
            "multiple resumable work items match; select an explicit ID: "
            + ", ".join(identifiers)
        )
    return matches[0]


def work_item_state_path(
    state_home: str | os.PathLike[str], scope_id: str, work_item_id: str
) -> Path:
    root = Path(_validated_path_text(state_home, label="state home"))
    if not root.is_absolute():
        raise StateHomeError("state home must be absolute")
    if not isinstance(scope_id, str) or _SCOPE_ID_PATTERN.fullmatch(scope_id) is None:
        raise StateStoreError("invalid scope ID")
    if (
        not isinstance(work_item_id, str)
        or _WORK_ITEM_ID_PATTERN.fullmatch(work_item_id) is None
    ):
        raise StateStoreError("invalid work-item ID")
    return root / scope_id / work_item_id


def scrub_state_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """Return a child environment without Exakt canonical-state locations."""
    if not isinstance(environ, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in environ.items()
    ):
        raise StateStoreError("environment must map text keys to text values")
    return {
        key: value
        for key, value in environ.items()
        if not key.casefold().startswith("exakt_state_")
    }


class JournalError(StateStoreError):
    """Base error for the canonical object store and event journal."""


class JournalConflictError(JournalError):
    """A writer's expected state root no longer names the journal head."""

    def __init__(
        self,
        message: str,
        *,
        expected_state_root: str,
        observed_state_root: str,
    ):
        super().__init__(message)
        self.expected_state_root = expected_state_root
        self.observed_state_root = observed_state_root


class JournalCorruptionError(JournalError):
    """Canonical state is incomplete, malformed, or digest-inconsistent."""


@dataclass(frozen=True)
class JournalDiagnostic:
    """Secret-safe location and category for the first invalid journal record."""

    code: str
    line: int
    byte_offset: int
    message: str

    def __contains__(self, value: object) -> bool:
        return isinstance(value, str) and (
            value in self.code or value in self.message
        )


@dataclass(frozen=True)
class JournalInspection:
    complete: bool
    truncated_tail: bool
    diagnostics: tuple[JournalDiagnostic, ...]
    records: tuple[dict[str, Any], ...]
    state_root: str
    valid_prefix_bytes: int
    last_record_hash: str | None
    head_sequence: int | None = None
    head_state_root: str | None = None
    head_pending: bool = False


@dataclass(frozen=True)
class JournalAppendResult:
    sequence: int
    payload_hash: str
    record_hash: str
    state_root: str


@dataclass(frozen=True)
class JournalRecoveryResult:
    state_root: str
    quarantined_path: Path
    recovered_records: int


def _validate_sha256_digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_DIGEST_PATTERN.fullmatch(value) is None:
        raise JournalError(f"{label} must be 64 lowercase hexadecimal characters")
    return value


def compute_state_root(
    scope_id: str,
    work_item_id: str,
    sequence: int,
    record_hash: str,
) -> str:
    """Compute the domain-separated root committed by one journal head."""
    if not isinstance(scope_id, str) or _SCOPE_ID_PATTERN.fullmatch(scope_id) is None:
        raise JournalError("invalid scope ID for state root")
    if (
        not isinstance(work_item_id, str)
        or _WORK_ITEM_ID_PATTERN.fullmatch(work_item_id) is None
    ):
        raise JournalError("invalid work-item ID for state root")
    if (
        not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 0
    ):
        raise JournalError("state-root sequence must be a non-negative integer")
    digest = _validate_sha256_digest(record_hash, label="record hash")
    preimage = (
        b"exakt-state-v1\n"
        + scope_id.encode("ascii")
        + b"\n"
        + work_item_id.encode("ascii")
        + b"\n"
        + _integer_to_decimal(sequence).encode("ascii")
        + b"\n"
        + digest.encode("ascii")
    )
    return sha256_hex(preimage)


def compute_genesis_state_root(scope_id: str, work_item_id: str) -> str:
    return compute_state_root(scope_id, work_item_id, 0, "0" * 64)


def _install_immutable_private_file(
    target: Path,
    payload: bytes,
    *,
    temporary_prefix: str,
    before_install: Callable[[], None] | None = None,
) -> None:
    """Install bytes without ever replacing an existing immutable name."""
    if not isinstance(payload, bytes):
        raise JournalError("immutable payload must be bytes")
    if len(payload) > MAX_PRIVATE_FILE_BYTES:
        raise JournalError(
            f"immutable payload exceeds {MAX_PRIVATE_FILE_BYTES} bytes"
        )
    _assert_private_directory(target.parent)

    def verify_existing() -> None:
        try:
            existing = _read_private_file_bytes(target)
        except FileNotFoundError:
            raise JournalCorruptionError(
                "immutable file disappeared during installation"
            ) from None
        if existing != payload:
            raise JournalCorruptionError(
                "immutable content-addressed name contains different bytes"
            )

    if target.is_symlink():
        raise UnsafeFilesystemError("refusing symlinked immutable file")
    if target.exists():
        verify_existing()
        return

    temporary = target.parent / (
        f"{temporary_prefix}{secrets.token_hex(8)}"
    )
    descriptor: int | None = None
    created = False
    try:
        descriptor = _exclusive_open(temporary, 0o600, cleanup_on_failure=True)
        created = True
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if _read_private_file_bytes(temporary) != payload:
            raise UnsafeFilesystemError("immutable temporary verification failed")
        if before_install is not None:
            before_install()
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            verify_existing()
        except (NotImplementedError, TypeError) as error:
            raise UnsafeFilesystemError(
                "no-overwrite immutable installation is unsupported"
            ) from error
        if target.is_symlink():
            raise UnsafeFilesystemError("immutable installation produced a symlink")
        verify_existing()
        temporary.unlink()
        created = False
        _sync_directory(target.parent)
        verify_existing()
    except OSError as error:
        raise UnsafeFilesystemError(
            f"cannot install immutable private file: {error}"
        ) from error
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        if created:
            with contextlib.suppress(OSError):
                if temporary.is_symlink() or temporary.exists():
                    temporary.unlink()


class DurableJournal:
    """Private immutable object store plus a CAS-protected canonical JSONL log."""

    def __init__(
        self,
        state_home: str | os.PathLike[str],
        scope_id: str,
        work_item_id: str,
        *,
        fault_hook: Callable[[str], None] | None = None,
    ):
        try:
            state_text = _validated_path_text(state_home, label="state home")
            self.state_home = Path(state_text).resolve(strict=True)
        except (StateHomeError, OSError, RuntimeError, UnicodeError, ValueError) as error:
            raise StateHomeError(f"cannot resolve journal state home: {error}") from error
        _assert_private_directory(self.state_home)
        if not isinstance(scope_id, str) or _SCOPE_ID_PATTERN.fullmatch(scope_id) is None:
            raise JournalError("invalid journal scope ID")
        if (
            not isinstance(work_item_id, str)
            or _WORK_ITEM_ID_PATTERN.fullmatch(work_item_id) is None
        ):
            raise JournalError("invalid journal work-item ID")
        self.scope_id = scope_id
        self.work_item_id = work_item_id
        if fault_hook is not None and not callable(fault_hook):
            raise JournalError("journal fault hook must be callable")
        self._fault_hook = fault_hook
        self.scope_root = self.state_home / scope_id
        self.work_root = self.scope_root / work_item_id
        self.object_store_root = self.work_root / "objects"
        self.objects_path = self.object_store_root / "sha256"
        self.quarantine_path = self.work_root / "quarantine"
        self.lock_path = self.work_root / "controller.lock"
        self.journal_path = self.work_root / "journal.jsonl"
        self.head_path = self.work_root / "head.json"
        self.genesis_state_root = compute_genesis_state_root(scope_id, work_item_id)
        for directory in (
            self.scope_root,
            self.work_root,
            self.object_store_root,
            self.objects_path,
        ):
            _create_private_directory(directory)
            _assert_private_directory(directory)
        self._initialize_head()

    def object_path(self, digest: str) -> Path:
        return self.objects_path / _validate_sha256_digest(
            digest, label="object digest"
        )

    def _fault(self, point: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)

    def _head_document(
        self, sequence: int, record_hash: str, state_root: str
    ) -> dict[str, Any]:
        return {
            "schema_version": "journal-head-v1",
            "scope_id": self.scope_id,
            "work_item_id": self.work_item_id,
            "sequence": sequence,
            "record_hash": record_hash,
            "state_root": state_root,
        }

    def _validate_head_document(self, document: Any) -> dict[str, Any]:
        expected_keys = {
            "schema_version",
            "scope_id",
            "work_item_id",
            "sequence",
            "record_hash",
            "state_root",
        }
        if not isinstance(document, dict) or set(document) != expected_keys:
            raise JournalCorruptionError("journal head violates its closed contract")
        if document["schema_version"] != "journal-head-v1":
            raise JournalCorruptionError("journal head has an unknown schema version")
        if (
            document["scope_id"] != self.scope_id
            or document["work_item_id"] != self.work_item_id
        ):
            raise JournalCorruptionError("journal head identity does not match its path")
        sequence = document["sequence"]
        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 0
        ):
            raise JournalCorruptionError("journal head sequence is invalid")
        try:
            record_hash = _validate_sha256_digest(
                document["record_hash"], label="journal-head record hash"
            )
            state_root = _validate_sha256_digest(
                document["state_root"], label="journal-head state root"
            )
        except JournalError as error:
            raise JournalCorruptionError("journal head digest is invalid") from error
        if sequence == 0 and record_hash != "0" * 64:
            raise JournalCorruptionError("genesis journal head hash is invalid")
        if compute_state_root(
            self.scope_id, self.work_item_id, sequence, record_hash
        ) != state_root:
            raise JournalCorruptionError("journal head state root is inconsistent")
        return document

    def _read_head_locked(self) -> dict[str, Any]:
        if self.head_path.is_symlink():
            raise UnsafeFilesystemError("refusing symlinked journal head")
        if not self.head_path.exists():
            raise JournalCorruptionError("journal head anchor is missing")
        try:
            payload = _read_private_file_bytes(self.head_path)
            document = parse_json_bytes(payload, require_canonical=True)
        except UnsafeFilesystemError:
            raise
        except (OSError, CanonicalStateError) as error:
            raise JournalCorruptionError(
                "journal head anchor is unreadable or noncanonical"
            ) from error
        return self._validate_head_document(document)

    def _write_head_locked(
        self,
        sequence: int,
        record_hash: str,
        state_root: str,
        *,
        enable_fault_hook: bool,
    ) -> None:
        document = self._validate_head_document(
            self._head_document(sequence, record_hash, state_root)
        )
        payload = canonical_json_bytes(document)
        _write_private_file_atomic(
            self.head_path,
            payload,
            before_replace=(
                (lambda: self._fault("before_head_replace"))
                if enable_fault_hook
                else None
            ),
            after_replace=(
                (lambda: self._fault("after_head_replace"))
                if enable_fault_hook
                else None
            ),
        )
        if canonical_json_bytes(self._read_head_locked()) != payload:
            raise JournalCorruptionError("published journal head verification failed")

    def _initialize_head(self) -> None:
        with _registry_lock(self.lock_path):
            if self.head_path.is_symlink():
                raise UnsafeFilesystemError("refusing symlinked journal head")
            if not self.head_path.exists():
                if self.journal_path.is_symlink() or self.journal_path.exists():
                    # Preserve existing bytes for read-only diagnosis. Missing
                    # authority is never reconstructed from the journal alone.
                    return
                self._write_head_locked(
                    0,
                    "0" * 64,
                    self.genesis_state_root,
                    enable_fault_hook=False,
                )

    def _put_object_bytes_locked(
        self, payload: bytes, *, enable_fault_hook: bool = False
    ) -> str:
        digest = sha256_hex(payload)
        _install_immutable_private_file(
            self.object_path(digest),
            payload,
            temporary_prefix=".object.tmp-",
            before_install=(
                lambda: self._fault("before_object_install")
                if enable_fault_hook
                else None
            ),
        )
        return digest

    def put_object(self, value: Any) -> str:
        payload = canonical_json_bytes(value)
        if len(payload) > MAX_PRIVATE_FILE_BYTES:
            raise JournalError(
                f"canonical object exceeds {MAX_PRIVATE_FILE_BYTES} bytes"
            )
        with _registry_lock(self.lock_path):
            return self._put_object_bytes_locked(payload)

    def _read_object_verified(self, digest: str) -> Any:
        expected = _validate_sha256_digest(digest, label="object digest")
        path = self.object_path(expected)
        if path.is_symlink():
            raise UnsafeFilesystemError("refusing symlinked content object")
        if not path.exists():
            raise JournalCorruptionError("referenced object is missing")
        try:
            payload = _read_private_file_bytes(path)
        except OSError as error:
            raise JournalCorruptionError("referenced object cannot be read") from error
        if sha256_hex(payload) != expected:
            raise JournalCorruptionError("referenced object digest is corrupt")
        try:
            return parse_json_bytes(payload, require_canonical=True)
        except CanonicalStateError as error:
            raise JournalCorruptionError(
                "referenced object is not canonical JSON"
            ) from error

    def read_object(self, digest: str) -> Any:
        return self._read_object_verified(digest)

    def _read_journal_bytes_locked(self) -> bytes:
        if self.journal_path.is_symlink():
            raise UnsafeFilesystemError("refusing symlinked canonical journal")
        if not self.journal_path.exists():
            return b""
        try:
            return _read_private_file_bytes(self.journal_path)
        except OSError as error:
            raise JournalCorruptionError("canonical journal cannot be read") from error

    @staticmethod
    def _diagnostic(
        code: str,
        line: int,
        byte_offset: int,
        message: str,
    ) -> JournalDiagnostic:
        return JournalDiagnostic(code, line, byte_offset, message)

    def _inspect_bytes(self, raw: bytes) -> JournalInspection:
        records: list[dict[str, Any]] = []
        diagnostics: list[JournalDiagnostic] = []
        offset = 0
        state_root = self.genesis_state_root
        previous_record_hash: str | None = None
        truncated_tail = False

        while offset < len(raw):
            line_number = len(records) + 1
            newline = raw.find(b"\n", offset)
            if newline < 0:
                truncated_tail = True
                diagnostics.append(
                    self._diagnostic(
                        "truncated_tail",
                        line_number,
                        offset,
                        "journal ends before the required LF framing byte",
                    )
                )
                break
            if len(records) >= MAX_JOURNAL_RECORDS:
                diagnostics.append(
                    self._diagnostic(
                        "record_limit",
                        line_number,
                        offset,
                        "journal exceeds the portable record-count limit",
                    )
                )
                break

            body = raw[offset:newline]
            try:
                record = parse_json_bytes(body, require_canonical=True)
            except CanonicalStateError:
                diagnostics.append(
                    self._diagnostic(
                        "noncanonical_record",
                        line_number,
                        offset,
                        "journal line is not canonical JSON",
                    )
                )
                break
            if not isinstance(record, dict) or "record_hash" not in record:
                diagnostics.append(
                    self._diagnostic(
                        "record_shape",
                        line_number,
                        offset,
                        "journal line lacks the stored-record envelope",
                    )
                )
                break

            event = dict(record)
            supplied_record_hash = event.pop("record_hash")
            try:
                _validate_sha256_digest(
                    supplied_record_hash, label="stored record hash"
                )
                _CONTRACTS.validate(event, "journal-event-v1")
            except (_ContractError, JournalError):
                diagnostics.append(
                    self._diagnostic(
                        "event_contract",
                        line_number,
                        offset,
                        "journal event violates its closed contract",
                    )
                )
                break

            expected_sequence = len(records) + 1
            if event["work_item_id"] != self.work_item_id:
                code = "work_item_mismatch"
                message = "journal event belongs to a different work item"
            elif event["sequence"] != expected_sequence:
                code = "sequence_mismatch"
                message = "journal sequence is not contiguous"
            elif event["previous_record_hash"] != previous_record_hash:
                code = "previous_hash_mismatch"
                message = "journal previous-record hash does not match its prefix"
            elif event["prior_state_root"] != state_root:
                code = "prior_root_mismatch"
                message = "journal prior state root does not match its prefix"
            else:
                code = ""
                message = ""
            if code:
                diagnostics.append(
                    self._diagnostic(code, line_number, offset, message)
                )
                break

            computed_record_hash = canonical_sha256(event)
            if supplied_record_hash != computed_record_hash:
                diagnostics.append(
                    self._diagnostic(
                        "record_hash_mismatch",
                        line_number,
                        offset,
                        "stored record hash does not match canonical event bytes",
                    )
                )
                break

            reference = event["payload"]
            digest = reference["payload_hash"]
            if reference["object_id"] != f"sha256:{digest}":
                diagnostics.append(
                    self._diagnostic(
                        "object_reference_mismatch",
                        line_number,
                        offset,
                        "payload object ID does not bind its digest",
                    )
                )
                break
            try:
                self._read_object_verified(digest)
            except UnsafeFilesystemError:
                diagnostics.append(
                    self._diagnostic(
                        "unsafe_object",
                        line_number,
                        offset,
                        "referenced object has unsafe filesystem metadata",
                    )
                )
                break
            except JournalCorruptionError:
                diagnostics.append(
                    self._diagnostic(
                        "object_corrupt",
                        line_number,
                        offset,
                        "referenced object is missing or corrupt",
                    )
                )
                break

            state_root = compute_state_root(
                self.scope_id,
                self.work_item_id,
                expected_sequence,
                supplied_record_hash,
            )
            previous_record_hash = supplied_record_hash
            records.append(record)
            offset = newline + 1

        complete = not diagnostics and offset == len(raw)
        return JournalInspection(
            complete=complete,
            truncated_tail=truncated_tail,
            diagnostics=tuple(diagnostics),
            records=tuple(records),
            state_root=state_root,
            valid_prefix_bytes=offset,
            last_record_hash=previous_record_hash,
        )

    def _bind_head_anchor_locked(
        self, inspection: JournalInspection
    ) -> JournalInspection:
        try:
            head = self._read_head_locked()
        except UnsafeFilesystemError:
            diagnostic = self._diagnostic(
                "unsafe_head",
                len(inspection.records) + 1,
                inspection.valid_prefix_bytes,
                "journal head has unsafe filesystem metadata",
            )
            return replace(
                inspection,
                complete=False,
                diagnostics=inspection.diagnostics + (diagnostic,),
            )
        except JournalCorruptionError:
            diagnostic = self._diagnostic(
                "head_corrupt",
                len(inspection.records) + 1,
                inspection.valid_prefix_bytes,
                "journal head is missing, noncanonical, or inconsistent",
            )
            return replace(
                inspection,
                complete=False,
                diagnostics=inspection.diagnostics + (diagnostic,),
            )

        head_sequence = head["sequence"]
        head_root = head["state_root"]
        added: JournalDiagnostic | None = None
        if head_sequence > len(inspection.records):
            added = self._diagnostic(
                "head_ahead_of_journal",
                len(inspection.records) + 1,
                inspection.valid_prefix_bytes,
                "journal is missing a suffix already committed by its head anchor",
            )
        elif head_sequence > 0:
            anchored_record = inspection.records[head_sequence - 1]
            anchored_hash = anchored_record["record_hash"]
            anchored_root = compute_state_root(
                self.scope_id,
                self.work_item_id,
                head_sequence,
                anchored_hash,
            )
            if (
                anchored_hash != head["record_hash"]
                or anchored_root != head_root
            ):
                added = self._diagnostic(
                    "head_divergence",
                    head_sequence,
                    inspection.valid_prefix_bytes,
                    "journal prefix diverges from its committed head anchor",
                )
        if added is None and len(inspection.records) > head_sequence + 1:
            added = self._diagnostic(
                "head_gap",
                head_sequence + 1,
                inspection.valid_prefix_bytes,
                "journal is more than one record ahead of its head anchor",
            )

        diagnostics = inspection.diagnostics
        if added is not None:
            diagnostics += (added,)
        pending = (
            added is None
            and inspection.complete
            and len(inspection.records) == head_sequence + 1
        )
        return replace(
            inspection,
            complete=inspection.complete and added is None,
            diagnostics=diagnostics,
            head_sequence=head_sequence,
            head_state_root=head_root,
            head_pending=pending,
        )

    def _inspect_locked(self) -> JournalInspection:
        return self._bind_head_anchor_locked(
            self._inspect_bytes(self._read_journal_bytes_locked())
        )

    def inspect(self) -> JournalInspection:
        with _registry_lock(self.lock_path):
            return self._inspect_locked()

    def append_event(
        self,
        payload: Any,
        *,
        event_id: str,
        event_type: str,
        actor: str,
        timestamp: str,
        idempotency_key: str,
        workflow_phase: str,
        run_status: str,
        expected_state_root: str,
    ) -> JournalAppendResult:
        expected = _validate_sha256_digest(
            expected_state_root, label="expected state root"
        )
        object_bytes = canonical_json_bytes(payload)
        if len(object_bytes) > MAX_PRIVATE_FILE_BYTES:
            raise JournalError(
                f"canonical object exceeds {MAX_PRIVATE_FILE_BYTES} bytes"
            )
        with _registry_lock(self.lock_path):
            current_raw = self._read_journal_bytes_locked()
            current = self._bind_head_anchor_locked(
                self._inspect_bytes(current_raw)
            )
            if not current.complete:
                code = current.diagnostics[0].code if current.diagnostics else "unknown"
                raise JournalCorruptionError(
                    f"canonical journal is incomplete or corrupt ({code})"
                )
            if expected != current.state_root:
                raise JournalConflictError(
                    "expected state root does not match the locked journal head",
                    expected_state_root=expected,
                    observed_state_root=current.state_root,
                )
            if current.head_pending:
                if current.last_record_hash is None:
                    raise JournalCorruptionError(
                        "pending journal head lacks a record hash"
                    )
                self._write_head_locked(
                    len(current.records),
                    current.last_record_hash,
                    current.state_root,
                    enable_fault_hook=True,
                )
                current = self._bind_head_anchor_locked(
                    self._inspect_bytes(current_raw)
                )
                if not current.complete or current.head_pending:
                    raise JournalCorruptionError(
                        "pending journal head reconciliation failed"
                    )

            payload_hash = sha256_hex(object_bytes)
            sequence = len(current.records) + 1
            event: dict[str, Any] = {
                "schema_version": "journal-event-v1",
                "event_id": event_id,
                "event_type": event_type,
                "work_item_id": self.work_item_id,
                "sequence": sequence,
                "actor": actor,
                "timestamp": timestamp,
                "idempotency_key": idempotency_key,
                "previous_record_hash": current.last_record_hash,
                "prior_state_root": current.state_root,
                "workflow_phase": workflow_phase,
                "run_status": run_status,
                "payload": {
                    "object_id": f"sha256:{payload_hash}",
                    "payload_hash": payload_hash,
                },
            }
            try:
                _CONTRACTS.validate(event, "journal-event-v1")
            except _ContractError as error:
                raise JournalError("journal event violates its closed contract") from error
            record_hash = canonical_sha256(event)
            record = dict(event)
            record["record_hash"] = record_hash
            candidate = current_raw + canonical_json_record(record)
            if len(candidate) > MAX_PRIVATE_FILE_BYTES:
                raise JournalError(
                    f"canonical journal exceeds {MAX_PRIVATE_FILE_BYTES} bytes"
                )
            installed_payload_hash = self._put_object_bytes_locked(
                object_bytes, enable_fault_hook=True
            )
            if installed_payload_hash != payload_hash:
                raise JournalCorruptionError(
                    "installed object digest differs from the validated event"
                )
            self._fault("after_object_install")
            candidate_inspection = self._inspect_bytes(candidate)
            next_root = compute_state_root(
                self.scope_id, self.work_item_id, sequence, record_hash
            )
            if (
                not candidate_inspection.complete
                or candidate_inspection.state_root != next_root
            ):
                raise JournalCorruptionError(
                    "candidate journal failed pre-publication verification"
                )
            _write_private_file_atomic(
                self.journal_path,
                candidate,
                before_replace=lambda: self._fault("before_journal_replace"),
                after_replace=lambda: self._fault("after_journal_replace"),
            )
            self._write_head_locked(
                sequence,
                record_hash,
                next_root,
                enable_fault_hook=True,
            )
            installed = self._inspect_locked()
            if (
                not installed.complete
                or installed.head_pending
                or installed.state_root != next_root
            ):
                raise JournalCorruptionError(
                    "published journal failed head verification"
                )
            return JournalAppendResult(
                sequence=sequence,
                payload_hash=payload_hash,
                record_hash=record_hash,
                state_root=next_root,
            )

    def _persist_quarantine_bytes_locked(
        self, origin_name: str, payload: bytes
    ) -> Path:
        _create_private_directory(self.quarantine_path)
        _assert_private_directory(self.quarantine_path)
        safe_origin = re.sub(r"[^A-Za-z0-9._-]+", "-", origin_name).strip(".-")
        if not safe_origin:
            safe_origin = "artifact"
        safe_origin = safe_origin[:80]
        digest = sha256_hex(payload)
        destination = self.quarantine_path / f"{safe_origin}-{digest}.quarantine"
        _install_immutable_private_file(
            destination,
            payload,
            temporary_prefix=".quarantine.tmp-",
        )
        return destination

    def quarantine_temp_files(self) -> tuple[Path, ...]:
        """Move known crash leftovers to private immutable quarantine names."""
        with _registry_lock(self.lock_path):
            sources: list[Path] = []
            scans = (
                (self.work_root, (".journal.jsonl.tmp-", ".head.json.tmp-")),
                (self.objects_path, (".object.tmp-", ".payload.tmp-")),
            )
            if self.quarantine_path.exists() or self.quarantine_path.is_symlink():
                _assert_private_directory(self.quarantine_path)
                scans += ((self.quarantine_path, (".quarantine.tmp-",)),)
            for directory, prefixes in scans:
                _assert_private_directory(directory)
                try:
                    entry_count = 0
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            entry_count += 1
                            if entry_count > MAX_JSON_NODES:
                                raise UnsafeFilesystemError(
                                    "crash-artifact scan exceeds the portable entry limit"
                                )
                            if any(
                                entry.name.startswith(prefix) for prefix in prefixes
                            ):
                                sources.append(Path(entry.path))
                except OSError as error:
                    raise UnsafeFilesystemError(
                        "cannot enumerate crash artifacts safely"
                    ) from error

            quarantined: list[Path] = []
            for source in sorted(sources, key=lambda item: item.as_posix()):
                try:
                    payload = _read_private_file_bytes(source)
                except (OSError, UnsafeFilesystemError) as error:
                    raise UnsafeFilesystemError(
                        "crash artifact has unsafe filesystem metadata"
                    ) from error
                destination = self._persist_quarantine_bytes_locked(
                    source.name, payload
                )
                try:
                    source.unlink()
                    _sync_directory(source.parent)
                except OSError as error:
                    raise UnsafeFilesystemError(
                        "cannot remove a quarantined crash artifact"
                    ) from error
                quarantined.append(destination)
            return tuple(quarantined)

    def recover_longest_valid_prefix(
        self, expected_state_root: str
    ) -> JournalRecoveryResult:
        """Explicitly publish a diagnosed prefix after preserving damaged bytes."""
        expected = _validate_sha256_digest(
            expected_state_root, label="expected recovery state root"
        )
        with _registry_lock(self.lock_path):
            raw = self._read_journal_bytes_locked()
            inspection = self._bind_head_anchor_locked(self._inspect_bytes(raw))
            if inspection.complete:
                raise JournalCorruptionError("canonical journal does not need recovery")
            if expected != inspection.state_root:
                raise JournalConflictError(
                    "expected recovery root does not match the valid journal prefix",
                    expected_state_root=expected,
                    observed_state_root=inspection.state_root,
                )
            if (
                inspection.head_state_root != inspection.state_root
                or inspection.head_sequence != len(inspection.records)
            ):
                raise JournalCorruptionError(
                    "recovery cannot discard or advance committed head authority"
                )
            preserved = self._persist_quarantine_bytes_locked(
                "journal.jsonl", raw
            )
            prefix = raw[: inspection.valid_prefix_bytes]
            _write_private_file_atomic(self.journal_path, prefix)
            installed = self._inspect_locked()
            if not installed.complete or installed.state_root != expected:
                raise JournalCorruptionError(
                    "recovered journal failed prefix verification"
                )
            return JournalRecoveryResult(
                state_root=installed.state_root,
                quarantined_path=preserved,
                recovered_records=len(installed.records),
            )


__all__ = [
    "AmbiguousResumeError",
    "CANONICAL_JSON_VERSION",
    "CanonicalStateError",
    "DurableJournal",
    "FilesystemCapabilities",
    "JournalAppendResult",
    "JournalConflictError",
    "JournalCorruptionError",
    "JournalDiagnostic",
    "JournalError",
    "JournalInspection",
    "JournalRecoveryResult",
    "MAX_INTEGER_DIGITS",
    "MAX_JOURNAL_RECORDS",
    "PreparedStateHome",
    "RepositoryIdentityError",
    "RepositoryRegistry",
    "RepositoryRelocationRequired",
    "StateHomeError",
    "StateStoreError",
    "UnsafeFilesystemError",
    "allocate_unique_work_item_id",
    "assert_no_case_collisions",
    "assert_safe_state_home_path",
    "canonical_json_bytes",
    "canonical_json_record",
    "canonical_sha256",
    "compare_and_swap_file",
    "compute_genesis_state_root",
    "compute_state_root",
    "make_repository_record",
    "new_repository_id",
    "new_work_item_id",
    "parse_json_bytes",
    "paths_overlap",
    "prepare_state_home",
    "probe_state_home_filesystem",
    "resolve_state_home",
    "sanitize_remote_url",
    "sanitize_remote_urls",
    "scope_id_for_repositories",
    "scrub_state_environment",
    "select_resume_candidate",
    "sha256_hex",
    "work_item_state_path",
]
