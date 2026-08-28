#!/usr/bin/env python3
"""Expand the V3 minimal extraction shape into the full Boons contract.

The V3 prompts have the LLM emit a compact shape to cut output tokens (~31-50%
fewer chars measured on the sample set):

    {"name": "Wings", "price": 10.0,
     "modifiers": [
       {"title": "Size", "type": "radio", "mandatory": "yes", "item_limit": "1",
        "choices": [{"name": "6pc", "price": "10"}, {"name": "12pc", "price": "16"}]}
     ]}

expand_menu() rebuilds the full Boons shape the review UI / DynamoDB expects:
 - item gets description "" and dietary_tags "" defaults when omitted, modifiers []
 - each modifier gets addonqty, option{option_id,name,price,unit} parallel arrays,
   and options_detail[{name,price,unit,submodifier}]
 - nested choice.choices → options_detail[i].submodifier (recursive)

This is the deterministic inverse of the minimal prompt: the model never has to
emit option_id (always ""), unit (always ""), the duplicated options_detail, or
addonqty — Python fills them in reliably, which also removes the option-shape and
price-type drift the LLM used to introduce.

The output of expand_menu() is exactly what PostValidator.validate() expects.
"""

from typing import Any


def expand_menu(categories: Any) -> list:
    """Expand a full minimal-shape menu (list of {category, items}) to Boons shape.

    Accepts either a raw list or a {"categories": [...]} wrapper. Returns a new
    list; the input is not mutated.
    """
    if isinstance(categories, dict) and isinstance(categories.get("categories"), list):
        categories = categories["categories"]
    if not isinstance(categories, list):
        return []

    out = []
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        out.append({
            "category": cat.get("category", ""),
            "items": [_expand_item(it) for it in cat.get("items", []) if isinstance(it, dict)],
        })
    return out


def _expand_item(item: dict) -> dict:
    """Expand one minimal item to the full 5-field Boons item shape."""
    expanded = {
        "name": item.get("name", ""),
        "price": item.get("price", 0.0),
        "description": item.get("description", "") or "",
        "dietary_tags": item.get("dietary_tags", "") or "",
        "modifiers": [_expand_modifier(m) for m in item.get("modifiers", []) if isinstance(m, dict)],
    }
    return expanded


def _expand_modifier(mod: dict) -> dict:
    """Expand one minimal modifier {title,type,mandatory,item_limit,choices} to Boons.

    Rebuilds option{option_id,name,price,unit} parallel arrays + options_detail,
    derives addonqty from mandatory, and recurses into nested choices → submodifier.
    """
    mod_type = mod.get("type", "radio")
    mandatory = mod.get("mandatory", "yes" if mod_type == "radio" else "no")
    # addonqty mirrors the historical Boons convention: "1" when mandatory, else "0".
    addonqty = "1" if str(mandatory).lower() in ("yes", "true", "1") else "0"
    item_limit = mod.get("item_limit", "1" if mod_type == "radio" else "0")

    choices = mod.get("choices", []) or []

    names, prices, units, option_ids = [], [], [], []
    options_detail = []

    for ch in choices:
        if not isinstance(ch, dict):
            continue
        name = ch.get("name", "")
        price = ch.get("price", "")  # minimal shape omits price when free → default ""
        if not isinstance(price, str):
            price = str(price)  # be tolerant if the model emitted a number
        unit = ch.get("unit", "") or ""

        names.append(name)
        prices.append(price)
        units.append(unit)
        option_ids.append("")

        # Nested choices → a full submodifier object; leaf → submodifier null.
        nested = ch.get("choices")
        if isinstance(nested, list) and nested:
            submodifier = _expand_modifier({
                "title": name,
                "type": ch.get("type", mod_type),
                "mandatory": ch.get("mandatory", mandatory),
                "item_limit": ch.get("item_limit", "1"),
                "choices": nested,
            })
        else:
            submodifier = None

        options_detail.append({
            "name": name,
            "price": price,
            "unit": unit,
            "submodifier": submodifier,
        })

    return {
        "title": mod.get("title", ""),
        "item_limit": str(item_limit),
        "mandatory": mandatory,
        "addonqty": addonqty,
        "type": mod_type,
        "option": {
            "option_id": option_ids,
            "name": names,
            "price": prices,
            "unit": units,
        },
        "options_detail": options_detail,
    }
