import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from auth import get_current_user
from backup_utils import (
    clear_directory,
    copy_tree,
    create_data_backup_archive,
    resolve_backup_archive_path,
    resolve_data_import_root,
    safe_extract_zip,
)
from db import Database, User, get_db


def create_admin_backup_router(
    *,
    data_backup_admin_emails: set[str],
    data_backup_lock: Lock,
    project_root: Path,
    data_dir: Path,
    reports_dir: Path,
    data_backup_archive_dir: Path,
    data_import_max_mb: int,
    data_import_max_bytes: int,
    has_running_background_tasks: Callable[[], bool],
    on_import_success: Callable[[Database], None],
) -> APIRouter:
    router = APIRouter()

    def ensure_data_backup_access(current_user: User) -> None:
        """Allow backup/import only for configured admin emails (or any user if unset)."""
        if not data_backup_admin_emails:
            return
        if current_user.email.lower() not in data_backup_admin_emails:
            raise HTTPException(status_code=403, detail="Backup/import access denied")

    @router.get("/api/admin/data-backup")
    def download_data_backup(current_user: User = Depends(get_current_user)):
        """Download full backup of data/ directory as zip archive."""
        ensure_data_backup_access(current_user)

        if not data_backup_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Backup/import is already in progress")

        try:
            archive_path = create_data_backup_archive()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to create backup: {exc}") from exc
        finally:
            data_backup_lock.release()

        return FileResponse(
            path=str(archive_path),
            media_type="application/zip",
            filename=archive_path.name,
        )

    @router.get("/api/admin/data-backups")
    def list_data_backups(current_user: User = Depends(get_current_user)):
        """List existing backup archives in data/backups."""
        ensure_data_backup_access(current_user)

        if not data_backup_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Backup/import is already in progress")

        try:
            data_backup_archive_dir.mkdir(parents=True, exist_ok=True)
            backups = []
            for archive in data_backup_archive_dir.glob("*.zip"):
                stat = archive.stat()
                backups.append({
                    "filename": archive.name,
                    "size_bytes": stat.st_size,
                    "created_at": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
                    "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
            backups.sort(key=lambda item: item["updated_at"], reverse=True)
            return {"backups": backups}
        finally:
            data_backup_lock.release()

    @router.get("/api/admin/data-backups/{filename}")
    def download_existing_data_backup(filename: str, current_user: User = Depends(get_current_user)):
        """Download an existing backup archive from data/backups."""
        ensure_data_backup_access(current_user)

        if not data_backup_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Backup/import is already in progress")

        try:
            archive_path = resolve_backup_archive_path(filename)
            if not archive_path.exists() or not archive_path.is_file():
                raise HTTPException(status_code=404, detail="Backup archive not found")
            return FileResponse(
                path=str(archive_path),
                media_type="application/zip",
                filename=archive_path.name,
            )
        finally:
            data_backup_lock.release()

    @router.delete("/api/admin/data-backups/{filename}")
    def delete_data_backup(filename: str, current_user: User = Depends(get_current_user)):
        """Delete a backup archive from data/backups."""
        ensure_data_backup_access(current_user)

        if not data_backup_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Backup/import is already in progress")

        try:
            archive_path = resolve_backup_archive_path(filename)
            if not archive_path.exists() or not archive_path.is_file():
                raise HTTPException(status_code=404, detail="Backup archive not found")
            archive_path.unlink()
            return {"status": "deleted", "filename": archive_path.name}
        finally:
            data_backup_lock.release()

    @router.post("/api/admin/data-import")
    async def import_data_backup(
        request: Request,
        mode: str = "replace",
        current_user: User = Depends(get_current_user),
        db: Database = Depends(get_db),
    ):
        """Import data/ directory from uploaded zip archive (replace or merge)."""
        ensure_data_backup_access(current_user)

        normalized_mode = (mode or "replace").lower()
        if normalized_mode not in {"replace", "merge"}:
            raise HTTPException(status_code=400, detail="Invalid mode. Use 'replace' or 'merge'")

        if has_running_background_tasks():
            raise HTTPException(
                status_code=409,
                detail="Stop active refresh/analysis tasks before importing backup",
            )

        if not data_backup_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Backup/import is already in progress")

        try:
            with tempfile.TemporaryDirectory(prefix="data-import-", dir=str(project_root)) as tmp:
                tmp_dir = Path(tmp)
                upload_path = tmp_dir / "upload.zip"

                total_bytes = 0
                with open(upload_path, "wb") as uploaded_file:
                    async for chunk in request.stream():
                        if not chunk:
                            continue
                        total_bytes += len(chunk)
                        if total_bytes > data_import_max_bytes:
                            raise HTTPException(
                                status_code=413,
                                detail=f"Archive is too large (max {data_import_max_mb} MB)",
                            )
                        uploaded_file.write(chunk)

                if total_bytes == 0:
                    raise HTTPException(status_code=400, detail="Request body is empty")

                if not zipfile.is_zipfile(upload_path):
                    raise HTTPException(status_code=400, detail="Uploaded file must be a valid ZIP archive")

                extract_dir = tmp_dir / "extract"
                extract_dir.mkdir(parents=True, exist_ok=True)
                safe_extract_zip(upload_path, extract_dir)

                import_root = resolve_data_import_root(extract_dir)
                source_files = [item for item in import_root.rglob("*") if item.is_file()]
                if not source_files:
                    raise HTTPException(status_code=400, detail="Archive does not contain data files")

                data_dir.mkdir(parents=True, exist_ok=True)
                if normalized_mode == "replace":
                    # Keep local backup history on server during restore.
                    clear_directory(data_dir, keep_names={"backups"})
                    data_dir.mkdir(parents=True, exist_ok=True)

                # Do not overwrite local backup archive store from imported snapshot.
                copied_files = copy_tree(import_root, data_dir, skip_top_level_dirs={"backups"})

            reports_dir.mkdir(parents=True, exist_ok=True)
            data_backup_archive_dir.mkdir(parents=True, exist_ok=True)
            on_import_success(db)

            return {
                "status": "ok",
                "mode": normalized_mode,
                "imported_files": copied_files,
                "size_bytes": total_bytes,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to import backup: {exc}") from exc
        finally:
            data_backup_lock.release()

    return router
