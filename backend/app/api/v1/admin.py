"""Admin-only operational endpoints (Phase 8) — currently just an on-demand
database backup trigger, alongside the nightly scheduled one."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import StaffAccount, StaffRole
from app.schemas.admin import BackupResult
from app.services.audit import record
from app.services.backup import run_backup

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/backups/run", response_model=BackupResult)
def trigger_backup(
    db: Session = Depends(get_db),
    account: StaffAccount = Depends(require_roles(StaffRole.admin)),
) -> BackupResult:
    """Run the same backup the nightly job runs, synchronously, on demand."""
    key = run_backup()
    record(db, actor_id=account.id, action="backup.run", detail={"key": key})
    db.commit()
    return BackupResult(key=key)
