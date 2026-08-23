"""VisionProvider interface and the strict result schema every provider must
return. Business logic (validation, repair, brand matching, review flags)
lives outside providers, so swapping NVIDIA ↔ Anthropic ↔ self-hosted YOLO
never touches the pipeline.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.models.base import BagType


class VisionDetectionItem(BaseModel):
    """One item the model claims to see on the tray."""

    item_name: str
    subcategory: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    estimated_quantity: str | None = None
    ocr_text: str | None = None
    bbox: dict[str, Any] | None = None


class VisionResult(BaseModel):
    """The strict JSON contract the model must produce."""

    detections: list[VisionDetectionItem]
    tray_notes: str | None = None


@dataclass
class ProviderResponse:
    """Raw provider output plus observability metadata. Parsing/validation of
    raw_text into VisionResult happens in app.services.analysis."""

    raw_text: str
    model: str
    latency_ms: int
    usage: dict[str, Any] = field(default_factory=dict)


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
