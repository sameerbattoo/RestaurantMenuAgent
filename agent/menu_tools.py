#!/usr/bin/env python3
"""Menu CRUD tools backed by DynamoDB persistence."""

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import boto3
from strands import tool

from utils import timed_tool

logger = logging.getLogger(__name__)

# ─── DynamoDB Client ──────────────────────────────────────────────────────────

_TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "restaurant-menus")
_REGION = os.environ.get("AWS_REGION", "us-west-2")
_table = boto3.resource("dynamodb", region_name=_REGION).Table(_TABLE_NAME)


# ─── Type Conversion Helpers ──────────────────────────────────────────────────

def _from_dynamodb(obj):
    """Convert DynamoDB Decimals to native Python types."""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, dict):
        return {k: _from_dynamodb(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_dynamodb(i) for i in obj]
    return obj


def _to_dynamodb(obj):
    """Convert floats to Decimals for DynamoDB storage."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_dynamodb(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dynamodb(i) for i in obj]
    return obj


# ─── DB Operations ────────────────────────────────────────────────────────────

def _get_menu(file_name: str) -> Optional[dict]:
    """Fetch a menu by file_name. Returns None if not found."""
    resp = _table.get_item(Key={"file_name": file_name})
    item = resp.get("Item")
    return _from_dynamodb(item) if item else None


def _put_menu(file_name: str, menu_data: dict, s3_key: str = None):
    """Upsert a menu. Strips raw_text, adds timestamp. Optionally stores S3 key."""
    item = _to_dynamodb(menu_data)
    item["file_name"] = file_name
    item["processed_at"] = datetime.now(timezone.utc).isoformat()
    item.pop("raw_text", None)
    if s3_key:
        item["s3_key"] = s3_key
    _table.put_item(Item=item)


def _compute_stats(menu_data: dict) -> dict:
    """Compute summary stats from menu data."""
    total_items = 0
    categories = []
    prices = []

    for cat in menu_data.get("categories", []):
        categories.append(cat.get("name", "Unknown"))
        items = cat.get("items", [])
        total_items += len(items)
        for item in items:
            price = item.get("price")
            if price is not None:
                try:
                    prices.append(float(str(price).replace("$", "").replace(",", "")))
                except (ValueError, TypeError):
                    pass

    return {
        "total_categories": len(categories),
        "categories": categories,
        "total_items": total_items,
        "price_range": {
            "min": f"${min(prices):.2f}" if prices else "N/A",
            "max": f"${max(prices):.2f}" if prices else "N/A",
        },
    }


# ─── Tools ────────────────────────────────────────────────────────────────────

@tool
@timed_tool
def get_current_menu(file_name: str) -> str:
    """
    Retrieve a menu from the database by file name.

    Args:
        file_name: The source file name to look up

    Returns:
        JSON menu data, or error if not found
    """
    menu = _get_menu(file_name)
    if menu:
        return json.dumps(menu, indent=2)
    return json.dumps({"error": f"No menu found for '{file_name}'."})


@tool
@timed_tool
def list_restaurant_menus() -> str:
    """
    List all restaurant menus stored in the database.

    Returns:
        JSON with total count and list of menus (file name, restaurant, items, date)
    """
    items = []
    response = _table.scan(
        ProjectionExpression="file_name, restaurant_name, metadata, processed_at"
    )
    items.extend(response.get("Items", []))

    while "LastEvaluatedKey" in response:
        response = _table.scan(
            ProjectionExpression="file_name, restaurant_name, metadata, processed_at",
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    menus = sorted(
        [
            {
                "file_name": _from_dynamodb(item).get("file_name", ""),
                "restaurant_name": _from_dynamodb(item).get("restaurant_name", "Unknown"),
                "total_items": _from_dynamodb(item).get("metadata", {}).get("total_items", 0),
                "processed_at": _from_dynamodb(item).get("processed_at", ""),
            }
            for item in items
        ],
        key=lambda x: x.get("processed_at", ""),
        reverse=True,
    )

    return json.dumps({"total_menus": len(menus), "menus": menus}, indent=2)


@tool
@timed_tool
def add_menu_item(
    file_name: str,
    category: str,
    name: str,
    price: str,
    description: Optional[str] = None,
    dietary_info: Optional[str] = None,
) -> str:
    """
    Add a new item to a stored menu.

    Args:
        file_name: Menu file name to modify
        category: Category to add the item to (e.g., "Appetizers")
        name: Dish name
        price: Price (e.g., "12.99")
        description: Optional dish description
        dietary_info: Optional comma-separated dietary tags (e.g., "vegetarian, gluten-free")

    Returns:
        Confirmation or error
    """
    menu = _get_menu(file_name)
    if not menu:
        return json.dumps({"error": f"No menu found for '{file_name}'."})

    new_item = {
        "name": name,
        "price": price,
        "description": description or "",
        "dietary_info": [d.strip() for d in dietary_info.split(",")] if dietary_info else [],
    }

    # Find or create category
    found = False
    for cat in menu.get("categories", []):
        if cat.get("name", "").lower() == category.lower():
            cat.setdefault("items", []).append(new_item)
            found = True
            break

    if not found:
        menu.setdefault("categories", []).append({"name": category, "items": [new_item]})

    # Update metadata and persist
    menu.setdefault("metadata", {})["total_items"] = sum(
        len(c.get("items", [])) for c in menu["categories"]
    )
    _put_menu(file_name, menu)

    return json.dumps({"status": "Item added", "file_name": file_name, "category": category, "item": new_item}, indent=2)


@tool
@timed_tool
def remove_menu_item(file_name: str, category: str, item_name: str) -> str:
    """
    Remove an item from a stored menu.

    Args:
        file_name: Menu file name to modify
        category: Category containing the item
        item_name: Name of the dish to remove

    Returns:
        Confirmation or error
    """
    menu = _get_menu(file_name)
    if not menu:
        return json.dumps({"error": f"No menu found for '{file_name}'."})

    for cat in menu.get("categories", []):
        if cat.get("name", "").lower() == category.lower():
            before = len(cat.get("items", []))
            cat["items"] = [i for i in cat.get("items", []) if i.get("name", "").lower() != item_name.lower()]
            if len(cat["items"]) < before:
                menu.setdefault("metadata", {})["total_items"] = sum(
                    len(c.get("items", [])) for c in menu["categories"]
                )
                _put_menu(file_name, menu)
                return json.dumps({"status": "Item removed", "file_name": file_name, "removed": item_name})
            return json.dumps({"error": f"Item '{item_name}' not found in '{category}'."})

    return json.dumps({"error": f"Category '{category}' not found."})


@tool
@timed_tool
def update_menu_item(
    file_name: str,
    category: str,
    item_name: str,
    new_price: Optional[str] = None,
    new_description: Optional[str] = None,
    new_name: Optional[str] = None,
) -> str:
    """
    Update an existing menu item.

    Args:
        file_name: Menu file name to modify
        category: Category containing the item
        item_name: Current dish name
        new_price: New price (optional)
        new_description: New description (optional)
        new_name: New dish name (optional)

    Returns:
        Confirmation with updated item, or error
    """
    menu = _get_menu(file_name)
    if not menu:
        return json.dumps({"error": f"No menu found for '{file_name}'."})

    for cat in menu.get("categories", []):
        if cat.get("name", "").lower() == category.lower():
            for item in cat.get("items", []):
                if item.get("name", "").lower() == item_name.lower():
                    if new_price is not None:
                        item["price"] = new_price
                    if new_description is not None:
                        item["description"] = new_description
                    if new_name is not None:
                        item["name"] = new_name
                    _put_menu(file_name, menu)
                    return json.dumps({"status": "Item updated", "file_name": file_name, "item": item}, indent=2)
            return json.dumps({"error": f"Item '{item_name}' not found in '{category}'."})

    return json.dumps({"error": f"Category '{category}' not found."})


@tool
@timed_tool
def export_menu_json(file_name: str) -> str:
    """
    Export a menu's full data from the database.

    Args:
        file_name: Menu file name to export

    Returns:
        Full menu JSON or error
    """
    menu = _get_menu(file_name)
    if not menu:
        return json.dumps({"error": f"No menu found for '{file_name}'."})

    export = {k: v for k, v in menu.items() if k not in ("file_name", "processed_at")}
    return json.dumps({"status": "Menu exported", "file_name": file_name, "menu_data": export}, indent=2)


@tool
@timed_tool
def rename_restaurant(file_name: str, new_name: str) -> str:
    """
    Rename the restaurant for a stored menu.

    Args:
        file_name: The menu file name to update
        new_name: The new restaurant name

    Returns:
        Confirmation or error
    """
    menu = _get_menu(file_name)
    if not menu:
        return json.dumps({"error": f"No menu found for '{file_name}'."})

    old_name = menu.get("restaurant_name", "Unknown")
    menu["restaurant_name"] = new_name
    _put_menu(file_name, menu)

    return json.dumps({
        "status": "Restaurant renamed",
        "file_name": file_name,
        "old_name": old_name,
        "new_name": new_name,
    }, indent=2)


@tool
@timed_tool
def rename_category(file_name: str, old_category: str, new_category: str) -> str:
    """
    Rename a category in a stored menu.

    Args:
        file_name: The menu file name to update
        old_category: Current category name
        new_category: New category name

    Returns:
        Confirmation or error
    """
    menu = _get_menu(file_name)
    if not menu:
        return json.dumps({"error": f"No menu found for '{file_name}'."})

    for cat in menu.get("categories", []):
        if cat.get("name", "").lower() == old_category.lower():
            cat["name"] = new_category
            _put_menu(file_name, menu)
            return json.dumps({
                "status": "Category renamed",
                "file_name": file_name,
                "old_name": old_category,
                "new_name": new_category,
            }, indent=2)

    return json.dumps({"error": f"Category '{old_category}' not found."})


@tool
@timed_tool
def merge_menu(target_file_name: str, source_file_name: str) -> str:
    """
    Merge menu items from a source file into an existing target restaurant entry.
    New categories are added, existing categories get new items appended (no duplicates).
    Tracks which source files have been merged in metadata.

    Args:
        target_file_name: The existing menu file to merge INTO
        source_file_name: The menu file to merge FROM (will be removed after merge)

    Returns:
        Summary of what was merged (new categories, new items added)
    """
    target = _get_menu(target_file_name)
    if not target:
        return json.dumps({"error": f"Target menu '{target_file_name}' not found."})

    source = _get_menu(source_file_name)
    if not source:
        return json.dumps({"error": f"Source menu '{source_file_name}' not found."})

    # Track merge stats
    new_categories = 0
    new_items = 0
    updated_categories = 0

    # Build a lookup of existing categories (case-insensitive)
    target_cats = {cat["name"].lower(): cat for cat in target.get("categories", [])}

    for src_cat in source.get("categories", []):
        cat_key = src_cat["name"].lower()

        if cat_key in target_cats:
            # Category exists — add non-duplicate items
            existing_items = {item["name"].lower() for item in target_cats[cat_key].get("items", [])}
            for item in src_cat.get("items", []):
                if item["name"].lower() not in existing_items:
                    target_cats[cat_key].setdefault("items", []).append(item)
                    new_items += 1
            if new_items > 0:
                updated_categories += 1
        else:
            # New category — add entirely
            target.setdefault("categories", []).append(src_cat)
            new_categories += 1
            new_items += len(src_cat.get("items", []))

    # Update metadata
    total_items = sum(len(c.get("items", [])) for c in target.get("categories", []))
    target.setdefault("metadata", {})["total_items"] = total_items

    # Recalculate price_range and dietary_options
    prices = []
    dietary_options = set()
    for cat in target.get("categories", []):
        for item in cat.get("items", []):
            price = item.get("price")
            if price is not None:
                try:
                    prices.append(float(str(price).replace("$", "").replace(",", "")))
                except (ValueError, TypeError):
                    pass
            for tag in item.get("dietary_info", []):
                if tag:
                    dietary_options.add(tag)

    target["metadata"]["price_range"] = {
        "min": f"${min(prices):.2f}" if prices else "N/A",
        "max": f"${max(prices):.2f}" if prices else "N/A",
    }
    target["metadata"]["dietary_options"] = sorted(dietary_options)

    # Track merged source files
    merged_files = target.get("metadata", {}).get("merged_from", [])
    if target_file_name not in merged_files:
        merged_files.append(target_file_name)
    if source_file_name not in merged_files:
        merged_files.append(source_file_name)
    target.setdefault("metadata", {})["merged_from"] = merged_files

    # Preserve S3 keys from both
    s3_keys = target.get("metadata", {}).get("s3_keys", [])
    if target.get("s3_key") and target["s3_key"] not in s3_keys:
        s3_keys.append(target["s3_key"])
    source_s3_key = source.get("s3_key")
    if source_s3_key and source_s3_key not in s3_keys:
        s3_keys.append(source_s3_key)
    target.setdefault("metadata", {})["s3_keys"] = s3_keys

    # Save updated target
    _put_menu(target_file_name, target)

    # Delete the source entry (it's now merged)
    _table.delete_item(Key={"file_name": source_file_name})

    return json.dumps({
        "status": "Menus merged successfully",
        "target_file": target_file_name,
        "merged_from": source_file_name,
        "new_categories_added": new_categories,
        "new_items_added": new_items,
        "updated_categories": updated_categories,
        "total_items_now": total_items,
        "all_source_files": merged_files,
    }, indent=2)


@tool
@timed_tool
def delete_menu(file_name: str) -> str:
    """
    Delete a restaurant menu from the database.

    Args:
        file_name: The menu file name to delete

    Returns:
        Confirmation or error
    """
    menu = _get_menu(file_name)
    if not menu:
        return json.dumps({"error": f"No menu found for '{file_name}'."})

    restaurant_name = menu.get("restaurant_name", "Unknown")
    total_items = sum(len(c.get("items", [])) for c in menu.get("categories", []))

    _table.delete_item(Key={"file_name": file_name})

    return json.dumps({
        "status": "Menu deleted",
        "file_name": file_name,
        "restaurant_name": restaurant_name,
        "items_removed": total_items,
    }, indent=2)
