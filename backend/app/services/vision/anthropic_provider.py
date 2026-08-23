"""Anthropic vision provider (optional alternative to NVIDIA).

Uses the Anthropic Messages API with base64 image input. Enabled with
VISION_PROVIDER=anthropic + ANTHROPIC_API_KEY; model via VISION_MODEL.
"""

import base64
import time
from typing import Any

import anthropic

from app.config import get_settings
from app.models.base import BagType
from app.services.vision.base import ProviderResponse, VisionProvider
from app.services.vision.prompts import build_analysis_prompt, build_repair_prompt


class AnthropicVisionProvider(VisionProvider):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.vision_model
        self._max_tokens = settings.vision_max_tokens

    def analyze(
        self,
        image_bytes: bytes,
        media_type: str,
        bag_type: BagType,
        vocabulary: list[str],
        prior_invalid_output: str | None = None,
    ) -> ProviderResponse:
        if prior_invalid_output is None:
            prompt = build_analysis_prompt(bag_type, vocabulary)
        else:
            prompt = build_repair_prompt(bag_type, vocabulary, prior_invalid_output)

        content: list[Any] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(image_bytes).decode(),
                },
            },
            {"type": "text", "text": prompt},
        ]
        started = time.monotonic()
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": content}],
        )
        latency_ms = int((time.monotonic() - started) * 1000)

        raw_text = next((b.text for b in message.content if b.type == "text"), "")
        return ProviderResponse(
            raw_text=raw_text,
            model=self._model,
            latency_ms=latency_ms,
            usage={
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            },
        )
