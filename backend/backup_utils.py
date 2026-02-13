import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_BACKUP_ARCHIVE_DIR = DATA_DIR / "backups"


def create_data_backup_archive() -> Path:
    """Create ZIP archive with full data folder and return archive path."""
    DATA_BACKUP_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_path = DATA_BACKUP_ARCHIVE_DIR / f"data_backup_{timestamp}.zip"

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in DATA_DIR.rglob("*"):
            if item.is_dir():
                continue
            if item.resolve() == archive_path.resolve():
                continue
            rel_path = item.relative_to(DATA_DIR)
            arcname = (Path("data") / rel_path).as_posix()
            archive.write(item, arcname=arcname)

    return archive_path


def safe_extract_zip(archive_path: Path, target_dir: Path) -> None:
    """Safely extract zip archive while preventing path traversal."""
    target_root = target_dir.resolve()
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.infolist():
            member_name = member.filename.replace("\\", "/")
            if not member_name or member_name.endswith("/"):
                continue

            member_path = Path(member_name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise HTTPException(status_code=400, detail="Archive contains unsafe paths")

            output_path = (target_root / member_path).resolve()
            if not str(output_path).startswith(str(target_root)):
                raise HTTPException(status_code=400, detail="Archive contains unsafe paths")

            output_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as src, open(output_path, "wb") as dst:
                shutil.copyfileobj(src, dst)


def resolve_data_import_root(extract_root: Path) -> Path:
    """Detect where imported data files are located inside extracted archive."""
    direct_data = extract_root / "data"
    if direct_data.is_dir():
        return direct_data

    top_level_entries = [
        entry for entry in extract_root.iterdir()
        if entry.name != "__MACOSX"
    ]

    top_level_dirs = [entry for entry in top_level_entries if entry.is_dir()]
    top_level_files = [entry for entry in top_level_entries if entry.is_file()]

    if len(top_level_dirs) == 1 and not top_level_files:
        nested_data = top_level_dirs[0] / "data"
        if nested_data.is_dir():
            return nested_data
        return top_level_dirs[0]

    return extract_root


def copy_tree(src_dir: Path, dst_dir: Path, skip_top_level_dirs: set[str] | None = None) -> int:
    """Copy directory tree from src to dst. Returns copied file count."""
    skip_top_level_dirs = {name.lower() for name in (skip_top_level_dirs or set())}
    copied_files = 0
    for src in src_dir.rglob("*"):
        rel = src.relative_to(src_dir)
        if rel.parts and rel.parts[0].lower() in skip_top_level_dirs:
            continue
        dst = dst_dir / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied_files += 1
    return copied_files


def clear_directory(dir_path: Path, keep_names: set[str] | None = None) -> None:
    """Remove all files/folders inside a directory."""
    keep_names = {name.lower() for name in (keep_names or set())}
    if not dir_path.exists():
        return
    for child in dir_path.iterdir():
        if child.name.lower() in keep_names:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def resolve_backup_archive_path(filename: str) -> Path:
    """Resolve and validate backup archive path inside data/backups."""
    raw_name = (filename or "").strip()
    if not raw_name:
        raise HTTPException(status_code=400, detail="Filename is required")

    candidate = Path(raw_name)
    if candidate.name != raw_name or candidate.suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    if ".." in candidate.parts:
        raise HTTPException(status_code=400, detail="Invalid backup filename")

    archive_path = (DATA_BACKUP_ARCHIVE_DIR / raw_name).resolve()
    backup_root = DATA_BACKUP_ARCHIVE_DIR.resolve()
    if not str(archive_path).startswith(str(backup_root)):
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    return archive_path
