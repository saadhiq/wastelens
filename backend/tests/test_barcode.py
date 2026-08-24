"""Dedicated barcode decode pass (Phase 5), independent of the vision
model. Decode-success is gated behind requires_zbar — see conftest.py and
DECISIONS.md for why libzbar isn't loadable on every dev host; it's always
correctly configured in the Docker image (libzbar0 installed explicitly),
verified there separately. The graceful-degradation tests run everywhere,
since "no barcode found" and "zbar unavailable" must be indistinguishable
to callers by design."""

import io

from PIL import Image

from app.services.barcode import decode_barcodes
from tests.conftest import requires_zbar


def test_garbage_bytes_return_empty_list_not_an_exception():
    assert decode_barcodes(b"this is not an image") == []


def test_plain_image_with_no_barcode_returns_empty_list():
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), color=(200, 200, 200)).save(buf, format="PNG")
    assert decode_barcodes(buf.getvalue()) == []


@requires_zbar
def test_decodes_a_real_qr_code():
    import qrcode

    img = qrcode.make("8901030826501")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    assert decode_barcodes(buf.getvalue()) == ["8901030826501"]
