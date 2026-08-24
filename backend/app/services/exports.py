"""CSV/PDF report exports for analysts (Phase 7): profiles, top items, top
brands, and quality — each downloadable via the existing analytics
endpoints' `format=csv|pdf` query param, alongside their default JSON.

Every report's underlying data goes through services/profiling.py's
gated_query — the same hard consent/is_sensitive/rejected-detection gate
the household consumption layer (Phase 6) uses. This module deliberately
does NOT change what the plain JSON responses of top-items/top-brands/
quality already return (those are unchanged, pre-existing Phase 3/5
endpoints) — only the export path is new, and only the export path is
gated. See DECISIONS.md for why the two intentionally diverge.
"""

import csv
import datetime as dt
import io
import uuid
from typing import Any

from fpdf import FPDF, XPos, YPos
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Brand, Capture, Detection, ReviewStatus, UserWasteProfile
from app.services.profiling import _resident_consents, gated_query


def _since(days: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


# --- Gated report data -------------------------------------------------


def gated_top_items(db: Session, days: int, limit: int = 10) -> list[tuple[str, int]]:
    name = func.coalesce(Detection.corrected_item_name, Detection.item_name)
    query = (
        gated_query(name.label("name"), func.count().label("count"))
        .where(Capture.captured_at >= _since(days), name != "unidentified_item")
        .group_by(name)
        .order_by(func.count().desc())
        .limit(limit)
    )
    return [(n, c) for n, c in db.execute(query)]


def gated_top_brands(db: Session, days: int, limit: int = 10) -> list[tuple[str, int]]:
    query = (
        gated_query(Brand.name, func.count().label("count"))
        .join(Brand, Detection.matched_brand_id == Brand.id)
        .where(Capture.captured_at >= _since(days))
        .group_by(Brand.name)
        .order_by(func.count().desc())
        .limit(limit)
    )
    return [(n, c) for n, c in db.execute(query)]


def gated_quality_rows(db: Session, days: int) -> list[tuple[str, int, float, int, int]]:
    """(item_name, detections, avg_confidence, reviewed, corrected)."""
    query = (
        gated_query(
            Detection.item_name,
            func.count().label("detections"),
            func.avg(Detection.confidence).label("avg_confidence"),
            func.sum(case((Detection.review_status != ReviewStatus.unreviewed, 1), else_=0)).label(
                "reviewed"
            ),
            func.sum(case((Detection.review_status == ReviewStatus.corrected, 1), else_=0)).label(
                "corrected"
            ),
        )
        .where(Capture.captured_at >= _since(days))
        .group_by(Detection.item_name)
        .order_by(func.count().desc())
        .limit(50)
    )
    return [
        (
            r.item_name,
            r.detections,
            float(r.avg_confidence or 0.0),
            int(r.reviewed),
            int(r.corrected),
        )
        for r in db.execute(query)
    ]


def gated_profile_rows(
    db: Session, resident_id: uuid.UUID, weeks: int
) -> list[UserWasteProfile] | None:
    """None if the resident doesn't exist or doesn't currently consent —
    an export never emits a household's profile without live consent, even
    though UserWasteProfile itself (services/aggregation.py, which predates
    this gate) may still hold rows for them."""
    if not _resident_consents(db, resident_id):
        return None
    return list(
        db.scalars(
            select(UserWasteProfile)
            .where(UserWasteProfile.user_id == resident_id)
            .order_by(UserWasteProfile.week_start.desc())
            .limit(weeks)
        )
    )


# --- Format writers ------------------------------------------------------


def to_csv(headers: list[str], rows: list[Any]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def to_pdf(title: str, headers: list[str], rows: list[Any]) -> bytes:
    """A plain tabular report: title, generated-at timestamp, header row,
    data rows. Intentionally simple — this is a data export for analysts,
    not a branded document."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(
        0,
        6,
        f"Generated {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M UTC}",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.ln(4)

    col_width = pdf.epw / max(len(headers), 1)
    pdf.set_font("Helvetica", "B", 10)
    for h in headers:
        pdf.cell(col_width, 8, str(h), border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for row in rows:
        for cell in row:
            text = "" if cell is None else str(cell)
            pdf.cell(col_width, 7, text, border=1)
        pdf.ln()

    return bytes(pdf.output())
