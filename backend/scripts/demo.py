"""End-to-end demo: pushes 3 generated sample tray images through the live API.

Prerequisites: docker compose up, seed script run, NVIDIA_API_KEY set in .env.
Run from the host:  python backend/scripts/demo.py
(needs `pip install httpx pillow` locally, or run inside the api container)

It logs in as the bootstrap admin, creates a demo operator + resident + bags,
uploads one image per bag type (organic/polythene/paper), then polls each
capture until analysis completes and prints the detections.
"""

import io
import os
import sys
import time
import uuid

import httpx

API = os.environ.get("WASTELENS_API", "http://localhost:8000/api/v1")
ADMIN_EMAIL = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "admin@wastelens.io")
ADMIN_PASSWORD = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "change-me")


def make_sample_image(label: str, color: tuple[int, int, int]) -> bytes:
    """A simple labeled JPEG stand-in for a tray photo. Swap in real photos by
    dropping files into backend/scripts/samples/ (used if present)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 480), color)
    draw = ImageDraw.Draw(img)
    draw.text((20, 220), f"sample tray: {label}", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def load_image(bag_type: str, fallback_color: tuple[int, int, int]) -> bytes:
    sample_dir = os.path.join(os.path.dirname(__file__), "samples")
    for ext in ("jpg", "jpeg", "png"):
        path = os.path.join(sample_dir, f"{bag_type}.{ext}")
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
    return make_sample_image(bag_type, fallback_color)


def main() -> int:
    client = httpx.Client(base_url=API, timeout=60)

    # 1. Login as admin
    resp = client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    resp.raise_for_status()
    admin_headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    # 2. Ensure a station operator exists (captures require that role)
    op_email = "demo-operator@wastelens.io"
    resp = client.post(
        "/auth/staff",
        headers=admin_headers,
        json={
            "email": op_email,
            "full_name": "Demo Operator",
            "password": "demo-operator-pass",
            "role": "station_operator",
        },
    )
    if resp.status_code not in (201, 409):
        resp.raise_for_status()
    resp = client.post("/auth/login", json={"email": op_email, "password": "demo-operator-pass"})
    resp.raise_for_status()
    op_headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    # 3. Demo resident + one bag per type
    suffix = uuid.uuid4().hex[:6]
    resp = client.post(
        "/users",
        headers=admin_headers,
        json={
            "name": f"Demo Household {suffix}",
            "phone": f"+9477{int(time.time()) % 10_000_000:07d}",
            "address": "42 Demo Lane",
        },
    )
    resp.raise_for_status()
    user_id = resp.json()["id"]
    print(f"resident: {user_id}")

    plan = [
        ("organic", (60, 120, 40)),
        ("polythene", (40, 80, 160)),
        ("paper", (150, 120, 80)),
    ]
    capture_ids: list[str] = []
    for bag_type, color in plan:
        tag = f"DEMO-{suffix}-{bag_type}"
        resp = client.post(
            "/bags",
            headers=admin_headers,
            json={"user_id": user_id, "bag_type": bag_type, "tag_id": tag},
        )
        resp.raise_for_status()

        image = load_image(bag_type, color)
        resp = client.post(
            "/captures",
            headers={**op_headers, "Idempotency-Key": f"demo-{suffix}-{bag_type}"},
            data={"bag_tag_id": tag, "station_id": "demo-station-1"},
            files={"image": (f"{bag_type}.jpg", image, "image/jpeg")},
        )
        resp.raise_for_status()
        capture_id = resp.json()["id"]
        capture_ids.append(capture_id)
        print(f"capture enqueued: {bag_type} -> {capture_id}")

    # 4. Poll until analysis finishes
    deadline = time.time() + 300
    pending = set(capture_ids)
    while pending and time.time() < deadline:
        time.sleep(5)
        for cid in list(pending):
            detail = client.get(f"/captures/{cid}", headers=op_headers).json()
            if detail["analysis_status"] in ("done", "failed"):
                pending.discard(cid)
                print(f"\n=== capture {cid}: {detail['analysis_status']} ===")
                for d in detail.get("detections", []):
                    flag = " [needs review]" if d["needs_review"] else ""
                    print(
                        f"  - {d['item_name']} (conf {d['confidence']:.2f})"
                        f"{flag} ocr={d['ocr_text']!r}"
                    )
    if pending:
        print(f"\ntimed out waiting for: {sorted(pending)}")
        return 1
    print("\ndemo complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
