#!/usr/bin/env python3
"""Deterministic post-validation for extracted menu JSON.

Replicates the LLM-based validation prompt (post_validation_prompt.txt) in pure Python.
Runs in milliseconds instead of the 3+ minutes an LLM needs to re-emit the full menu JSON.

Validation separates items into VALID and INVALID based on flexible criteria:

MANDATORY (must be present and valid):
  - Category: non-empty "category" name + "items" array.
  - Item: non-empty "name", numeric "price" (0.0 allowed), string "description" (may be "").

OPTIONAL (validated only if present):
  - Modifiers (Boons/n8n nested shape): non-empty title, type in {radio, checkbox},
    option{option_id,name,price,unit} with aligned array lengths and string prices,
    options_detail of length N with required keys (name/price/unit/submodifier).
  - Legacy modifier shape ({name, options, sub_modifiers}) is accepted, not flagged.

Only structural / functionality-breaking problems are reported as errors. Format flexibility
(string vs number for item_limit/mandatory/addonqty, missing optional fields, empty
descriptions, boolean representations, legacy shapes) is NOT an error.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PostValidator:
    """Validates extracted menu JSON and separates valid from invalid items.

    Usage:
        validator = PostValidator()
        result = validator.validate(menu_categories)   # list of category dicts
        # result["valid_categories"]  -> clean items, ready to persist
        # result["invalid_items"]     -> items with structural errors
        # result["validation_summary"], result["issues_summary"]
    """

    VALID_MODIFIER_TYPES = {"radio", "checkbox"}

    def validate(self, categories: Any) -> dict:
        """Validate a complete menu (list of category objects).

        Args:
            categories: The extracted menu — a list of {"category", "items"} dicts.
                        (Also tolerates a dict with a "categories" key.)

        Returns:
            Dict matching the post_validation_prompt.txt response contract:
            validation_summary, valid_categories, invalid_items, issues_summary.
        """
        categories = self._coerce_categories(categories)

        valid_categories: list[dict] = []
        invalid_items: list[dict] = []
        issues_summary: list[dict] = []
        total_items = 0
        valid_count = 0

        for cat in categories:
            cat_name, cat_errors = self._validate_category_shell(cat)

            # A structurally broken category (no name / no items array) — all its
            # items (if any) are moved to invalid, category is not emitted as valid.
            items = cat.get("items") if isinstance(cat, dict) else None
            if cat_errors or not isinstance(items, list):
                # Report each category-level problem once.
                for err in cat_errors:
                    issues_summary.append(self._issue(cat_name, "", err))
                # Salvage any items we can see so they aren't silently dropped.
                if isinstance(items, list):
                    for item in items:
                        total_items += 1
                        invalid_items.append({
                            "original_category": cat_name,
                            "item": item,
                            "validation_errors": [
                                self._error("category", "Parent category is invalid",
                                            cat_name, "non-empty category with items array")
                            ],
                        })
                continue

            kept_items: list[dict] = []
            for item in items:
                total_items += 1
                item_errors = self._validate_item(item)

                if item_errors:
                    invalid_items.append({
                        "original_category": cat_name,
                        "item": item,
                        "validation_errors": item_errors,
                    })
                    item_name = item.get("name", "") if isinstance(item, dict) else ""
                    for err in item_errors:
                        issues_summary.append(self._issue(cat_name, item_name, err))
                else:
                    valid_count += 1
                    kept_items.append(item)  # preserved unchanged (modifiers intact)

            # Only emit the category if it has at least one valid item.
            if kept_items:
                valid_categories.append({"category": cat_name, "items": kept_items})

        invalid_count = len(invalid_items)
        status = "PASS" if invalid_count == 0 else "FAIL"

        return {
            "validation_summary": {
                "total_items_processed": total_items,
                "valid_items_count": valid_count,
                "invalid_items_count": invalid_count,
                "validation_status": status,
                "overall_quality": self._quality(total_items, invalid_count),
            },
            "valid_categories": valid_categories,
            "invalid_items": invalid_items,
            "issues_summary": issues_summary,
        }

    # ─── Category validation ────────────────────────────────────────────────

    def _validate_category_shell(self, cat: Any) -> tuple[str, list[dict]]:
        """Check the category has a non-empty name and an items array.

        Returns (category_name, list_of_error_dicts).
        """
        errors: list[dict] = []
        if not isinstance(cat, dict):
            return "", [self._error("category", "Category is not an object",
                                    type(cat).__name__, "category object")]

        name = cat.get("category")
        cat_name = name if isinstance(name, str) else ""
        if not isinstance(name, str) or not name.strip():
            errors.append(self._error("category", "Category name is missing or not a non-empty string",
                                      name, "non-empty string"))

        if not isinstance(cat.get("items"), list):
            errors.append(self._error("items", "Category 'items' is missing or not an array",
                                      type(cat.get("items")).__name__, "array"))

        return cat_name, errors

    # ─── Item validation ──────────────────────────────────────────────────────

    def _validate_item(self, item: Any) -> list[dict]:
        """Validate a single item's mandatory fields and optional modifiers.

        Returns a list of error dicts (empty = valid).
        """
        errors: list[dict] = []

        if not isinstance(item, dict):
            return [self._error("item", "Item is not an object", str(item), "item object")]

        # name — mandatory, non-empty string
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(self._error("name", "Item name is missing or not a non-empty string",
                                      name, "non-empty string"))

        # price — mandatory, valid number (bool is not a number); 0.0 allowed
        price = item.get("price")
        if not self._is_number(price):
            errors.append(self._error("price", "Item price is missing or not a valid number",
                                      price, "number (0.0 allowed)"))

        # description — mandatory field but may be empty string; must be a string if present.
        # Missing description is tolerated per prompt ("can be missing/empty").
        description = item.get("description")
        if description is not None and not isinstance(description, str):
            errors.append(self._error("description", "Description must be a string",
                                      description, "string (\"\" if none)"))

        # modifiers — optional; validate structure only if present and non-empty
        modifiers = item.get("modifiers")
        if modifiers is not None:
            if not isinstance(modifiers, list):
                errors.append(self._error("modifiers", "Modifiers must be an array",
                                          type(modifiers).__name__, "array"))
            else:
                for idx, mod in enumerate(modifiers):
                    errors.extend(self._validate_modifier(mod, path=f"modifiers[{idx}]"))

        return errors

    # ─── Modifier validation ───────────────────────────────────────────────────

    def _validate_modifier(self, mod: Any, path: str) -> list[dict]:
        """Validate a modifier group (Boons/n8n shape). Legacy shape is accepted as-is.

        Only reports structural problems (e.g. misaligned option arrays). Format
        flexibility (string vs number, boolean representations) is NOT flagged.
        """
        errors: list[dict] = []

        if not isinstance(mod, dict):
            return [self._error(path, "Modifier must be an object",
                                type(mod).__name__, "modifier object")]

        # Legacy shape: {name, options, sub_modifiers}. Accept without error —
        # downstream converts it. Do not validate its internals.
        if "option" not in mod and "options" in mod:
            return []

        # Gap 4 — title SHOULD be a non-empty string label for the group.
        title = mod.get("title")
        if title is not None and (not isinstance(title, str) or not title.strip()):
            errors.append(self._error(f"{path}.title", "Modifier title must be a non-empty string",
                                      title, "non-empty string group label"))

        # Gap 1 — type, if present, must be one of the accepted values.
        mod_type = mod.get("type")
        if mod_type is not None and mod_type not in self.VALID_MODIFIER_TYPES:
            errors.append(self._error(f"{path}.type", "Modifier type is not a recognized value",
                                      mod_type, "one of: radio, checkbox"))

        option = mod.get("option")
        options_detail = mod.get("options_detail")

        # If neither Boons structure is present, there's nothing structural to
        # validate (title/type/etc. are format-flexible and not mandatory here).
        if option is None and options_detail is None:
            return errors

        # Validate the Boons option{} parallel arrays.
        n: Optional[int] = None
        if option is not None:
            if not isinstance(option, dict):
                errors.append(self._error(f"{path}.option", "option must be an object",
                                          type(option).__name__, "object with parallel arrays"))
            else:
                array_fields = ["option_id", "name", "price", "unit"]
                lengths = {}
                for field in array_fields:
                    val = option.get(field)
                    if val is None:
                        # A partial option{} is a structural problem — the arrays
                        # must exist together to be usable downstream.
                        errors.append(self._error(
                            f"{path}.option.{field}",
                            "option array is missing",
                            None, f"array aligned with option.name",
                        ))
                    elif not isinstance(val, list):
                        errors.append(self._error(
                            f"{path}.option.{field}",
                            "option field must be an array",
                            type(val).__name__, "array",
                        ))
                    else:
                        lengths[field] = len(val)

                # All present arrays must share the same length N.
                if lengths and len(set(lengths.values())) > 1:
                    errors.append(self._error(
                        f"{path}.option",
                        "option arrays are misaligned (option_id/name/price/unit must be same length)",
                        {k: v for k, v in lengths.items()},
                        "all arrays same length N",
                    ))
                n = lengths.get("name")

                # Gap 2 — every option.price entry must be a STRING ("" if free).
                price_arr = option.get("price")
                if isinstance(price_arr, list):
                    for pi, pv in enumerate(price_arr):
                        if not isinstance(pv, str):
                            errors.append(self._error(
                                f"{path}.option.price[{pi}]",
                                "option price must be a string",
                                pv, 'string (e.g. "2.50", or "" if included)',
                            ))

        # options_detail must be a list of length N (when N is known).
        if options_detail is not None:
            if not isinstance(options_detail, list):
                errors.append(self._error(f"{path}.options_detail",
                                          "options_detail must be an array",
                                          type(options_detail).__name__, "array of length N"))
            else:
                if n is not None and len(options_detail) != n:
                    errors.append(self._error(
                        f"{path}.options_detail",
                        "options_detail length does not match option.name length",
                        len(options_detail), n,
                    ))
                # Validate each entry's required keys, then recurse into submodifiers.
                for i, od in enumerate(options_detail):
                    if not isinstance(od, dict):
                        errors.append(self._error(
                            f"{path}.options_detail[{i}]",
                            "options_detail entry must be an object",
                            type(od).__name__, "object",
                        ))
                        continue

                    # Gap 3 — each entry must carry name, price, unit, submodifier keys.
                    for req_key in ("name", "price", "unit", "submodifier"):
                        if req_key not in od:
                            errors.append(self._error(
                                f"{path}.options_detail[{i}].{req_key}",
                                "options_detail entry is missing a required key",
                                None, f"{req_key} present (submodifier may be null)",
                            ))

                    # Gap 2 (mirror) — options_detail price must also be a string.
                    od_price = od.get("price")
                    if od_price is not None and not isinstance(od_price, str):
                        errors.append(self._error(
                            f"{path}.options_detail[{i}].price",
                            "options_detail price must be a string",
                            od_price, 'string (e.g. "2.50", or "" if included)',
                        ))

                    sub = od.get("submodifier")
                    if sub is not None:
                        errors.extend(self._validate_modifier(
                            sub, path=f"{path}.options_detail[{i}].submodifier"))

        return errors

    # ─── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _is_number(value: Any) -> bool:
        """True for int/float (a valid price). Bool is explicitly rejected."""
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @staticmethod
    def _coerce_categories(categories: Any) -> list:
        """Accept either a raw list of categories or a {'categories': [...]} wrapper."""
        if isinstance(categories, dict) and isinstance(categories.get("categories"), list):
            return categories["categories"]
        if isinstance(categories, list):
            return categories
        return []

    @staticmethod
    def _error(field: str, issue: str, current_value: Any, expected_value: Any) -> dict:
        """Build an error record matching the prompt's validation_errors[] shape."""
        return {
            "type": "ERROR",
            "field": field,
            "issue": issue,
            "current_value": current_value,
            "expected_value": expected_value,
        }

    @staticmethod
    def _issue(category: str, item_name: str, error: dict) -> dict:
        """Build an issues_summary[] record from an error, matching the prompt shape."""
        return {
            "type": "ERROR",
            "category": category,
            "item": item_name,
            "field": error.get("field", ""),
            "issue": error.get("issue", ""),
            "suggestion": f"Fix {error.get('field', 'field')}: expected {error.get('expected_value', '')}",
            "current_value": error.get("current_value"),
            "expected_value": error.get("expected_value"),
        }

    @staticmethod
    def _quality(total: int, invalid: int) -> str:
        """Grade overall quality from the invalid ratio (matches prompt's enum)."""
        if total == 0:
            return "POOR"
        invalid_ratio = invalid / total
        if invalid_ratio == 0:
            return "EXCELLENT"
        if invalid_ratio <= 0.05:
            return "GOOD"
        if invalid_ratio <= 0.20:
            return "FAIR"
        return "POOR"


def validate_menu(categories: Any) -> dict:
    """Convenience wrapper — validate a menu's categories with a fresh PostValidator."""
    return PostValidator().validate(categories)
