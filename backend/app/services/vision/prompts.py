"""Bag-type-specific prompt construction for the vision labeler.

The vocabulary is ALWAYS passed in from the item_vocabulary table — never
hardcoded here. Each bag type gets tailored guidance (degraded vegetables for
organic, OCR emphasis for packaging, conservatism for general waste).
"""

import json

from app.models.base import BagType

_JSON_CONTRACT = """
Respond with ONLY a JSON object — no markdown fences, no commentary — matching:
{
  "detections": [
    {
      "item_name": "<one name from the allowed vocabulary>",
      "subcategory": "<optional finer description or null>",
      "confidence": <float 0.0-1.0>,
      "estimated_quantity": "<e.g. '2 pieces', 'a handful', or null>",
      "ocr_text": "<any readable printed text on this item, verbatim, or null>",
      "bbox": null
    }
  ],
  "tray_notes": "<overall observations about the tray, or null>"
}
Every item_name MUST be one of the allowed vocabulary values. If an item does
not match any vocabulary entry, use "unidentified_item" and describe it in
subcategory. Report one detection per distinct item type visible.
""".strip()

_BAG_GUIDANCE: dict[BagType, str] = {
    BagType.organic: (
        "This tray holds ORGANIC kitchen waste. Identify specific vegetables and food "
        "scraps even when cut, peeled, wilted, or partially decomposed — peels, ends, "
        "leaves, and shells are the norm, not whole produce. Distinguish look-alikes "
        "carefully (e.g. cabbage leaf vs other greens; beetroot peel is deep red-purple)."
    ),
    BagType.polythene: (
        "This tray holds PLASTIC/POLYTHENE packaging waste. Identify the item type AND "
        "read any printed brand or product text (OCR). Report partial or garbled text "
        "exactly as seen in ocr_text — do not guess missing letters. Wrappers may be "
        "crumpled, folded, or torn."
    ),
    BagType.paper: (
        "This tray holds PAPER waste. Identify the item type AND read any printed brand "
        "or product text (OCR). Report partial text as-is in ocr_text. Boxes may be "
        "flattened or torn."
    ),
    BagType.general: (
        "This tray holds GENERAL mixed waste. Be conservative: only name an item when "
        "clearly identifiable, otherwise prefer 'unidentified_item'. Never guess."
    ),
}


def build_analysis_prompt(bag_type: BagType, vocabulary: list[str]) -> str:
    """The primary instruction sent alongside the tray image."""
    return (
        f"You are an expert waste-sorting analyst for a recycling facility.\n"
        f"{_BAG_GUIDANCE[bag_type]}\n\n"
        f"Allowed vocabulary for item_name: {json.dumps(sorted(vocabulary))}\n\n"
        f"{_JSON_CONTRACT}"
    )


def build_repair_prompt(bag_type: BagType, vocabulary: list[str], invalid_output: str) -> str:
    """Second-chance prompt when the first response failed JSON validation."""
    return (
        f"Your previous response was not valid JSON matching the required schema.\n"
        f"Previous response:\n{invalid_output}\n\n"
        f"Fix it. {_JSON_CONTRACT}\n"
        f"Allowed vocabulary for item_name: {json.dumps(sorted(vocabulary))}"
    )
