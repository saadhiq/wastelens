"""Vision provider factory. VISION_PROVIDER selects the implementation;
everything downstream depends only on the VisionProvider interface."""

from app.config import get_settings
from app.services.vision.base import (
    LocalYoloProvider,
    ProviderResponse,
    VisionDetectionItem,
    VisionProvider,
    VisionResult,
)


def get_vision_provider() -> VisionProvider:
    provider = get_settings().vision_provider.lower()
    if provider == "nvidia":
        from app.services.vision.nvidia_provider import NvidiaVisionProvider

        return NvidiaVisionProvider()
    if provider == "anthropic":
        from app.services.vision.anthropic_provider import AnthropicVisionProvider

        return AnthropicVisionProvider()
    if provider == "local_yolo":
        return LocalYoloProvider()
    raise ValueError(f"Unknown VISION_PROVIDER: {provider!r}")


__all__ = [
    "LocalYoloProvider",
    "ProviderResponse",
    "VisionDetectionItem",
    "VisionProvider",
    "VisionResult",
    "get_vision_provider",
]
