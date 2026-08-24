"""Prompt version selection (Phase 5): paper/polythene get the v2 packaging
extraction contract, organic/general stay on v1. Pure unit tests, no DB."""

from app.models.base import BagType
from app.services.vision.prompts import (
    build_analysis_prompt,
    build_repair_prompt,
    get_prompt_version,
)


class TestPromptVersion:
    def test_organic_and_general_are_v1(self):
        assert get_prompt_version(BagType.organic) == "v1"
        assert get_prompt_version(BagType.general) == "v1"

    def test_paper_and_polythene_are_v2(self):
        assert get_prompt_version(BagType.paper) == "v2"
        assert get_prompt_version(BagType.polythene) == "v2"


_PACKAGING_FIELDS = (
    "brand_text",
    "product_name_text",
    "pack_size_text",
    "barcode_text",
    "material_type",
)


class TestV2ContractFields:
    def test_polythene_prompt_asks_for_packaging_fields(self):
        prompt = build_analysis_prompt(BagType.polythene, ["chips_packet"])
        for field in _PACKAGING_FIELDS:
            assert field in prompt

    def test_paper_prompt_asks_for_packaging_fields(self):
        prompt = build_analysis_prompt(BagType.paper, ["cardboard_carton"])
        for field in _PACKAGING_FIELDS:
            assert field in prompt

    def test_organic_prompt_does_not_ask_for_packaging_fields(self):
        prompt = build_analysis_prompt(BagType.organic, ["banana_peel"])
        for field in _PACKAGING_FIELDS:
            assert field not in prompt

    def test_general_prompt_does_not_ask_for_packaging_fields(self):
        prompt = build_analysis_prompt(BagType.general, ["mixed_scrap"])
        for field in _PACKAGING_FIELDS:
            assert field not in prompt


class TestRepairPromptMatchesVersion:
    def test_repair_for_polythene_keeps_v2_contract(self):
        prompt = build_repair_prompt(BagType.polythene, ["chips_packet"], "not json")
        assert "brand_text" in prompt

    def test_repair_for_organic_keeps_v1_contract(self):
        prompt = build_repair_prompt(BagType.organic, ["banana_peel"], "not json")
        assert "brand_text" not in prompt
