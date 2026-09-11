from pathlib import Path


def _iter_candidate_dirs(anchor: Path):
    current = anchor.absolute()
    base_dir = current if current.is_dir() else current.parent
    yield base_dir
    yield from base_dir.parents


def discover_project_root(anchor: Path) -> Path:
    candidates = list(_iter_candidate_dirs(anchor))

    for candidate in candidates:
        if (candidate / "backend").exists() and ((candidate / "frontend").exists() or (candidate / ".env").exists()):
            return candidate

    for candidate in candidates:
        if candidate.name == "backend":
            return candidate.parent

    fallback_index = min(2, len(candidates) - 1)
    return candidates[fallback_index]


PROJECT_ROOT = discover_project_root(Path(__file__))
BACKEND_ROOT = PROJECT_ROOT / "backend"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
STATIC_ROOT = BACKEND_ROOT / "static"
LOG_ROOT = BACKEND_ROOT / "logs"