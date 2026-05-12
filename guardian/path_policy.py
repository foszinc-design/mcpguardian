"""Path allow-list policy for MCPGuardian Phase 5A.

The MCP control-plane adapter can be invoked by an LLM client. Do not let tool
arguments become arbitrary filesystem access. Every user-supplied path must be
inside an allowed root, and writable run artifacts must stay inside runs_dir.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class PathPolicyError(ValueError):
    """Raised when a path is outside the configured trust boundary."""


@dataclass(frozen=True)
class PathPolicy:
    allowed_roots: tuple[Path, ...]

    @classmethod
    def from_roots(cls, roots: Iterable[str | Path]) -> "PathPolicy":
        normalized: list[Path] = []
        for root in roots:
            p = Path(root).expanduser()
            try:
                resolved = p.resolve(strict=False)
            except OSError:
                resolved = p.absolute()
            normalized.append(resolved)
        if not normalized:
            raise PathPolicyError("At least one allowed root is required.")
        return cls(tuple(normalized))

    def resolve_allowed(self, raw_path: str | Path, *, must_exist: bool = False) -> Path:
        path = Path(raw_path).expanduser()
        try:
            resolved = path.resolve(strict=must_exist)
        except FileNotFoundError:
            raise
        except OSError:
            resolved = path.absolute()
        if not self.is_allowed(resolved):
            allowed = ", ".join(str(root) for root in self.allowed_roots)
            raise PathPolicyError(f"Path is outside MCPGuardian allowed roots: {resolved}. allowed_roots=[{allowed}]")
        return resolved

    def is_allowed(self, path: str | Path) -> bool:
        candidate = Path(path).expanduser()
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            resolved = candidate.absolute()
        for root in self.allowed_roots:
            if _is_relative_to(resolved, root):
                return True
        return False


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        # Windows paths are case-insensitive. This fallback also makes tests
        # deterministic across platforms when path strings differ only by case.
        return str(path).casefold().startswith(str(root).casefold().rstrip("/\\") + "/")
