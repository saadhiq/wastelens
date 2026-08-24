"""Unit tests for prompt building, JSON parsing/validation, and the cost guard.
No infrastructure required."""

import json

import pytest
from pydantic import ValidationError

from app.models.base import BagType
from app.services.analysis import parse_vision_result
from app.services.cost_guard import CostCapExceeded, register_cv_call
from app.services.vision.prompts import build_analysis_prompt, build_repair_prompt

VOCAB = ["banana_peel", "onion_peel", "unidentified_item"]


# --- prompt building -------------------------------------------------------


def test_prompt_contains_vocabulary_and_guidance():
    prompt = build_analysis_prompt(BagType.organic, VOCAB)
    for item in VOCAB:
        assert item in prompt
    assert "ORGANIC" in prompt
    assert "JSON" in prompt


def test_each_bag_type_gets_distinct_guidance():
    prompts = {bt: build_analysis_prompt(bt, VOCAB) for bt in BagType}
    # Phase 5: polythene/paper moved to the v2 packaging-extraction contract
    # (brand_text/product_name_text/pack_size_text/barcode_text as
    # structured fields), so they no longer mention "OCR" as a concept —
    # they ask for the specific fields that used to be dumped into it.
    assert "brand_text" in prompts[BagType.polythene]
    assert "brand_text" in prompts[BagType.paper]
    assert "conservative" in prompts[BagType.general].lower()
    assert len({p for p in prompts.values()}) == 4  # all different


def test_repair_prompt_embeds_invalid_output():
    prompt = build_repair_prompt(BagType.paper, VOCAB, "not json {{{")
    assert "not json {{{" in prompt
    assert "banana_peel" in prompt


# --- JSON parsing / validation ---------------------------------------------


def _valid_payload() -> str:
    return json.dumps(
        {
            "detections": [
                {
                    "item_name": "banana_peel",
                    "subcategory": None,
                    "confidence": 0.91,
                    "estimated_quantity": "2 pieces",
                    "ocr_text": None,
                    "bbox": None,
                }
            ],
            "tray_notes": "mostly fruit waste",
        }
    )


def test_parse_valid_json():
    result = parse_vision_result(_valid_payload())
    assert result.detections[0].item_name == "banana_peel"
    assert result.tray_notes == "mostly fruit waste"


def test_parse_strips_markdown_fences():
    fenced = f"```json\n{_valid_payload()}\n```"
    result = parse_vision_result(fenced)
    assert len(result.detections) == 1


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_vision_result("I see a banana peel and some onions!")


def test_parse_rejects_out_of_range_confidence():
    bad = json.dumps({"detections": [{"item_name": "x", "confidence": 1.7}]})
    with pytest.raises(ValidationError):
        parse_vision_result(bad)


# --- cost guard -------------------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.counts: dict[str, int] = {}

    def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key, ttl):
        pass


def test_cost_guard_allows_under_cap(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(
        "app.services.cost_guard.redis_lib.Redis",
        type("R", (), {"from_url": staticmethod(lambda *a, **k: fake)}),
    )
    assert register_cv_call() == 1
    assert register_cv_call() == 2


def test_cost_guard_blocks_over_cap(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(
        "app.services.cost_guard.redis_lib.Redis",
        type("R", (), {"from_url": staticmethod(lambda *a, **k: fake)}),
    )
    monkeypatch.setattr(
        "app.services.cost_guard.get_settings",
        lambda: type("S", (), {"redis_url": "redis://x", "cv_daily_call_cap": 2})(),
    )
    register_cv_call()
    register_cv_call()
    with pytest.raises(CostCapExceeded):
        register_cv_call()
