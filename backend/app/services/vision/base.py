"""VisionProvider interface and the strict result schema every provider must
return. Business logic (validation, repair, brand matching, review flags)
lives outside providers, so swapping NVIDIA ↔ Anthropic ↔ self-hosted YOLO
never touches the pipeline.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.models.base import BagType


class VisionDetectionItem(BaseModel):
    """One item the model claims to see on the tray.

    brand_text/product_name_text/pack_size_text/barcode_text/material_type
    (Phase 5) are only ever populated by the v2 paper/polythene prompts
    (see vision/prompts.py) — organic/general keep the v1 contract and
    leave these None. All optional here regardless of which prompt was
    used, since parsing must succeed either way.
    """

    item_name: str
    subcategory: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    estimated_quantity: str | None = None
    ocr_text: str | None = None
    bbox: dict[str, Any] | None = None
    brand_text: str | None = None
    product_name_text: str | None = None
    pack_size_text: str | None = None
    barcode_text: str | None = None
    material_type: str | None = None


class VisionResult(BaseModel):
    """The strict JSON contract the model must produce."""

    detections: list[VisionDetectionItem]
    tray_notes: str | None = None


@dataclass
class ProviderResponse:
    """Raw provider output plus observability metadata. Parsing/validation of
    raw_text into VisionResult happens in app.services.analysis.

    input_tokens/output_tokens/cost_usd/model_version/raw_response were added
    in Phase 2 so app.services.analysis can persist a full InferenceRun row
    per attempt without digging through `usage` (whose keys differ per
    provider — NVIDIA's OpenAI-compatible response uses prompt_tokens/
    completion_tokens, Anthropic's uses input_tokens/output_tokens; `usage`
    itself is kept as-is for backwards compatibility). cost_usd is left None
    by both providers today — no per-token pricing table exists yet, so
    computing it would be inventing data rather than reading it."""

    raw_text: str
    model: str
    latency_ms: int
    usage: dict[str, Any] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None
    model_version: str | None = None
    raw_response: dict[str, Any] | None = None


class VisionProvider(ABC):
    """Analyze one tray image and return the model's raw response.

    Implementations must send the prompt built by app.services.vision.prompts
    and return whatever text the model produced — even if malformed, so the
    caller can attempt a repair round and persist raw output for debugging.
    """

    @abstractmethod
    def analyze(
        self,
        image_bytes: bytes,
        media_type: str,
        bag_type: BagType,
        vocabulary: list[str],
        prior_invalid_output: str | None = None,
    ) -> ProviderResponse:
        """Run detection. When prior_invalid_output is given, issue a repair
        request asking the model to fix its previous malformed JSON."""


class LocalYoloProvider(VisionProvider):
    """Phase-2 stub: self-hosted YOLO/SAM pipeline.

    Planned implementation: run object detection + segmentation locally,
    map class labels onto the vocabulary, and OCR crops with a text model.
    Exists now so the factory and config surface stay stable.
    """

    def analyze(
        self,
        image_bytes: bytes,
        media_type: str,
        bag_type: BagType,
        vocabulary: list[str],
        prior_invalid_output: str | None = None,
    ) -> ProviderResponse:
        raise NotImplementedError("LocalYoloProvider lands in Phase 2")
