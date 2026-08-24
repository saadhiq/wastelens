"""Bag-type-specific prompt construction for the vision labeler.

The vocabulary is ALWAYS passed in from the item_vocabulary table — never
hardcoded here. Each bag type gets tailored guidance (degraded vegetables for
organic, OCR emphasis for packaging, conservatism for general waste).

Phase 5: paper and polythene moved to a v2 contract that asks the model to
read brand/product/pack-size/barcode text and material type as first-class
fields, instead of dumping everything into ocr_text — packaging trays carry
far more extractable value per image than organic ones. Organic and general
stay on v1; nothing about their behavior changes. Both versions are kept
here (v1 never deleted) specifically so InferenceRun.prompt_version lets us
compare detection accuracy across versions once real data exists — see
DECISIONS.md.
"""

import json

from app.models.base import BagType

# --- v1: original single-field OCR contract --------------------------------

_JSON_CONTRACT_V1 = """
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

_BAG_GUIDANCE_V1: dict[BagType, str] = {
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

# --- v2: packaging extraction contract (paper, polythene) ------------------
# Organic trays give categories; packaging trays give brands, pack sizes,
# and barcodes — the highest-value, most commercially useful signal in the
# system. This contract asks for it directly instead of leaving it buried
# in free-text ocr_text for a downstream regex to maybe find.

_JSON_CONTRACT_V2 = """
Respond with ONLY a JSON object — no markdown fences, no commentary — matching:
{
  "detections": [
    {
      "item_name": "<one name from the allowed vocabulary>",
      "subcategory": "<optional finer description or null>",
      "confidence": <float 0.0-1.0>,
      "estimated_quantity": "<e.g. '2 pieces', 'a handful', or null>",
      "ocr_text": "<any readable printed text on this item, verbatim, or null>",
      "brand_text": "<the brand/manufacturer name as printed, verbatim, or null>",
      "product_name_text": "<the specific product name as printed, verbatim, or null>",
      "pack_size_text": "<printed weight/volume/count e.g. '200g', '1L', '6 pack', or null>",
      "barcode_text": "<the printed barcode digits if legible, verbatim, or null>",
      "material_type": "<e.g. 'PET plastic', 'cardboard', 'foil', 'LDPE film', or null>",
      "bbox": null
    }
  ],
  "tray_notes": "<overall observations about the tray, or null>"
}
Every item_name MUST be one of the allowed vocabulary values. If an item does
not match any vocabulary entry, use "unidentified_item" and describe it in
subcategory. Report one detection per distinct item type visible.
brand_text, product_name_text, pack_size_text, and barcode_text are each
independent fields — fill in whichever are legible and leave the rest null,
do not merge them into ocr_text. Never invent text you cannot actually read.
""".strip()

_BAG_GUIDANCE_V2: dict[BagType, str] = {
    BagType.polythene: (
        "This tray holds PLASTIC/POLYTHENE packaging waste. This is a packaging-heavy "
        "category: reading brand_text, product_name_text, pack_size_text, and "
        "barcode_text accurately is the primary goal, more valuable than the item "
        "category alone. Read every visible piece of printed text — brand logo, product "
        "name, weight/volume, and any barcode digits — as separate fields, verbatim, "
        "never guessing missing characters. Wrappers may be crumpled, folded, or torn; "
        "report whatever fragment is legible rather than skipping the field."
    ),
    BagType.paper: (
        "This tray holds PAPER waste. This is a packaging-heavy category: reading "
        "brand_text, product_name_text, pack_size_text, and barcode_text accurately is "
        "the primary goal, more valuable than the item category alone. Read every "
        "visible piece of printed text — brand logo, product name, weight/volume, and "
        "any barcode digits — as separate fields, verbatim, never guessing missing "
        "characters. Boxes may be flattened or torn; report whatever fragment is "
        "legible rather than skipping the field."
    ),
}

# Which contract version applies to each bag type. Organic/general stay on
# v1 deliberately — there's no packaging text to extract there, and v1's
# simpler contract keeps their token cost and failure surface unchanged.
_PROMPT_VERSION: dict[BagType, str] = {
    BagType.organic: "v1",
    BagType.general: "v1",
    BagType.polythene: "v2",
    BagType.paper: "v2",
}


def get_prompt_version(bag_type: BagType) -> str:
    """The prompt contract version build_analysis_prompt/build_repair_prompt
    will use for this bag type — providers read this to stamp
    InferenceRun.prompt_version, so accuracy is comparable across versions
    once enough reviewed data exists."""
    return _PROMPT_VERSION[bag_type]


def _contract_and_guidance(bag_type: BagType) -> tuple[str, str]:
    if get_prompt_version(bag_type) == "v2":
        return _JSON_CONTRACT_V2, _BAG_GUIDANCE_V2[bag_type]
    return _JSON_CONTRACT_V1, _BAG_GUIDANCE_V1[bag_type]


def build_analysis_prompt(bag_type: BagType, vocabulary: list[str]) -> str:
    """The primary instruction sent alongside the tray image."""
    contract, guidance = _contract_and_guidance(bag_type)
    return (
        f"You are an expert waste-sorting analyst for a recycling facility.\n"
        f"{guidance}\n\n"
        f"Allowed vocabulary for item_name: {json.dumps(sorted(vocabulary))}\n\n"
        f"{contract}"
    )


def build_repair_prompt(bag_type: BagType, vocabulary: list[str], invalid_output: str) -> str:
    """Second-chance prompt when the first response failed JSON validation."""
    contract, _ = _contract_and_guidance(bag_type)
    return (
        f"Your previous response was not valid JSON matching the required schema.\n"
        f"Previous response:\n{invalid_output}\n\n"
        f"Fix it. {contract}\n"
        f"Allowed vocabulary for item_name: {json.dumps(sorted(vocabulary))}"
    )
