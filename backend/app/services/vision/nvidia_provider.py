"""NVIDIA NIM vision provider (default).

Talks to NVIDIA's OpenAI-compatible chat-completions endpoint
(https://integrate.api.nvidia.com/v1) with the image inlined as a base64 data
URI. Model is configured via NVIDIA_VISION_MODEL (e.g.
meta/llama-4-maverick-17b-128e-instruct).
"""

import base64
import time

import httpx

from app.config import get_settings
from app.models.base import BagType
from app.services.vision.base import ProviderResponse, VisionProvider
from app.services.vision.prompts import build_analysis_prompt, build_repair_prompt


class NvidiaVisionProvider(VisionProvider):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.nvidia_api_key:
            raise RuntimeError("NVIDIA_API_KEY is not configured")
        self._base_url = settings.nvidia_base_url.rstrip("/")
        self._model = settings.nvidia_vision_model
        self._max_tokens = settings.vision_max_tokens
        self._api_key = settings.nvidia_api_key
        self._timeout = settings.vision_timeout_seconds

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

        data_uri = f"data:{media_type};base64,{base64.b64encode(image_bytes).decode()}"
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            "max_tokens": self._max_tokens,
            # Extraction task: keep sampling tight for stable JSON.
            "temperature": 0.2,
            "top_p": 0.9,
            "stream": False,
        }

        started = time.monotonic()
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
            json=payload,
            timeout=httpx.Timeout(float(self._timeout), connect=10.0),
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        response.raise_for_status()
        body = response.json()
        usage = body.get("usage", {})

        return ProviderResponse(
            raw_text=body["choices"][0]["message"]["content"],
            model=self._model,
            latency_ms=latency_ms,
            usage=usage,
            # OpenAI-compatible naming, distinct from Anthropic's below.
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            # The model NVIDIA actually resolved the request to, which can
            # differ subtly from the requested self._model.
            model_version=body.get("model"),
            raw_response=body,
        )
