"""Dedicated barcode/QR decode pass on the tray image — independent of the
vision model (Phase 5). A decoded barcode is ground truth and beats
whatever the model read via OCR into barcode_text; see services/analysis.py
for how the two are reconciled per detection.

Best-effort by design: a tray photo usually has zero decodable barcodes
(crumpled wrappers, no visible code, blur), and the zbar shared library
itself isn't guaranteed present on every host (see DECISIONS.md — this bit
local Windows dev; the Docker image installs libzbar0 explicitly). Neither
case should ever fail capture analysis, so every failure mode here resolves
to an empty list, not an exception.
"""

from app.core.logging import get_logger

log = get_logger(__name__)

try:
    from pyzbar.pyzbar import decode as _zbar_decode

    zbar_available = True
except Exception:  # pragma: no cover - exercised only when libzbar is missing
    _zbar_decode = None
    zbar_available = False
    log.warning("zbar_unavailable", detail="pyzbar/libzbar failed to load; barcode decode disabled")


def decode_barcodes(image_bytes: bytes) -> list[str]:
    """Decoded string values found in the image, in the order zbar reports
    them (roughly left-to-right, top-to-bottom), deduplicated while
    preserving that order. Empty when zbar is unavailable, the image is
    unreadable, or nothing decodable is visible — all indistinguishable to
    the caller by design, since none of them should change pipeline
    behavior beyond "no ground-truth barcode this time"."""
    if not zbar_available:
        return []

    from io import BytesIO

    from PIL import Image

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            results = _zbar_decode(image)
    except Exception:
        log.warning("barcode_decode_failed", exc_info=True)
        return []

    values: list[str] = []
    for result in results:
        try:
            value = result.data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if value and value not in values:
            values.append(value)
    return values
