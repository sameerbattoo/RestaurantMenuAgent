#!/usr/bin/env python3
"""Compare batch runs and generate a markdown comparison report.

Auto-discovers runs in batch_runs/ and generates a report similar to comparison-report.md.

Usage:
    python compare_runs.py                  # Compare all runs, output to stdout
    python compare_runs.py --last 3         # Last 3 runs only
    python compare_runs.py -o report.md     # Write to file
    python compare_runs.py -m sonnet        # Filter by model name
"""

import argparse
import json
import os
import sys
from datetime import datetime

BATCH_RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "batch_runs")


def discover_runs() -> dict[str, str]:
    """Auto-discover run folders, keep latest per model."""
    runs = {}
    if not os.path.isdir(BATCH_RUNS_DIR):
        return runs
    for entry in sorted(os.listdir(BATCH_RUNS_DIR)):
        full_path = os.path.join(BATCH_RUNS_DIR, entry)
        if not os.path.isdir(full_path) or not entry.startswith("run-"):
            continue
        parts = entry.split("-", 3)
        model_name = parts[3] if len(parts) >= 4 else entry
        display_name = model_name.replace("-", " ").title()
        runs[display_name] = entry
    return runs


def load_menu(run_dir: str, file_base: str) -> dict | None:
    path = os.path.join(BATCH_RUNS_DIR, run_dir, f"{file_base}_menu.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def analyze_menu(menu: dict | None) -> dict:
    if menu is None:
        return {"restaurant_name": "—", "categories": 0, "items": 0,
                "items_with_price": 0, "items_with_description": 0,
                "items_with_dietary": 0, "price_range": "—"}

    categories = menu.get("categories", [])
    total_items = 0
    items_with_price = 0
    items_with_desc = 0
    items_with_dietary = 0
    prices = []

    for cat in categories:
        for item in cat.get("items", []):
            total_items += 1
            price = item.get("price")
            if price and str(price) not in ("", "null", "None", "0"):
                items_with_price += 1
                try:
                    p = float(str(price).replace("$", "").replace(",", ""))
                    if p > 0:
                        prices.append(p)
                except (ValueError, TypeError):
                    pass
            desc = item.get("description")
            if desc and str(desc).strip() not in ("", "null", "None"):
                items_with_desc += 1
            dietary = item.get("dietary_info")
            if dietary and isinstance(dietary, list) and len(dietary) > 0:
                items_with_dietary += 1

    return {
        "restaurant_name": menu.get("restaurant_name", "—") or "—",
        "categories": len(categories),
        "items": total_items,
        "items_with_price": items_with_price,
        "items_with_description": items_with_desc,
        "items_with_dietary": items_with_dietary,
        "price_range": f"${min(prices):.2f} – ${max(prices):.2f}" if prices else "—",
    }


def get_menu_files(runs: dict) -> list[str]:
    for run_dir in runs.values():
        full_path = os.path.join(BATCH_RUNS_DIR, run_dir)
        if os.path.isdir(full_path):
            return sorted(
                f.replace("_menu.json", "")
                for f in os.listdir(full_path)
                if f.endswith("_menu.json") and not f.startswith("_")
            )
    return []


def generate_report(RUNS: dict) -> str:
    """Generate markdown comparison report."""
    models = list(RUNS.keys())
    menu_files = get_menu_files(RUNS)

    if not menu_files:
        return "No menu files found.\n"

    # Load run metadata and per-file results
    meta = {}
    file_durations = {}  # {model: {file_base: duration_seconds}}
    for model, run_dir in RUNS.items():
        log_path = os.path.join(BATCH_RUNS_DIR, run_dir, "_batch_run_log.json")
        if os.path.exists(log_path):
            with open(log_path) as f:
                log = json.load(f)
            total_cost = log.get("total_cost_usd", 0)
            if not total_cost and "results" in log:
                total_cost = sum(r.get("cost_usd", 0) for r in log["results"])
            meta[model] = {
                "cost": total_cost,
                "duration": log.get("total_duration_seconds", 0),
                "success": log.get("files_successful", 0),
                "total": log.get("files_processed", 0),
                "tokens": log.get("total_input_tokens", 0) + log.get("total_output_tokens", 0),
            }
            # Per-file durations
            file_durations[model] = {}
            for r in log.get("results", []):
                base = r.get("file", "").replace("_menu.json", "")
                base = os.path.splitext(base)[0] if "." in base else base
                file_durations[model][base] = r.get("duration_seconds", 0)

    # Build report
    lines = []
    lines.append(f"# Model Comparison Report\n")
    lines.append(f"**Generated:** {datetime.now().strftime('%B %d, %Y')}")
    lines.append(f"**Runs compared:** {', '.join(models)}")
    lines.append(f"**Files processed:** {len(menu_files)} restaurant menus\n")
    lines.append("---\n")

    # Overall Performance
    lines.append("## Overall Performance\n")
    header = "| Metric | " + " | ".join(models) + " |"
    sep = "|--------|" + "|".join(["-------"] * len(models)) + "|"
    lines.append(header)
    lines.append(sep)

    row = "| Success Rate | " + " | ".join(
        f"{meta.get(m,{}).get('success',0)}/{meta.get(m,{}).get('total',0)}" for m in models) + " |"
    lines.append(row)
    row = "| Duration | " + " | ".join(
        f"{meta.get(m,{}).get('duration',0):.1f}s" for m in models) + " |"
    lines.append(row)
    row = "| Total Tokens | " + " | ".join(
        f"{meta.get(m,{}).get('tokens',0):,}" for m in models) + " |"
    lines.append(row)
    row = "| **Total Cost** | " + " | ".join(
        f"**${meta.get(m,{}).get('cost',0):.4f}**" for m in models) + " |"
    lines.append(row)
    lines.append("")

    # Aggregate Quality
    totals = {m: {"items": 0, "price": 0, "desc": 0, "dietary": 0} for m in models}
    all_analyses = {}

    for file_base in menu_files:
        all_analyses[file_base] = {}
        for model, run_dir in RUNS.items():
            menu = load_menu(run_dir, file_base)
            a = analyze_menu(menu)
            all_analyses[file_base][model] = a
            totals[model]["items"] += a["items"]
            totals[model]["price"] += a["items_with_price"]
            totals[model]["desc"] += a["items_with_description"]
            totals[model]["dietary"] += a["items_with_dietary"]

    lines.append("---\n")
    lines.append("## Aggregate Quality Metrics\n")
    lines.append(header)
    lines.append(sep)

    row = "| **Total Items Extracted** | " + " | ".join(
        f"**{totals[m]['items']}**" for m in models) + " |"
    lines.append(row)
    row = "| Items with Price | " + " | ".join(
        f"{totals[m]['price']} ({totals[m]['price']*100//max(totals[m]['items'],1)}%)" for m in models) + " |"
    lines.append(row)
    row = "| Items with Description | " + " | ".join(
        f"{totals[m]['desc']} ({totals[m]['desc']*100//max(totals[m]['items'],1)}%)" for m in models) + " |"
    lines.append(row)
    row = "| Items with Dietary Info | " + " | ".join(
        f"{totals[m]['dietary']} ({totals[m]['dietary']*100//max(totals[m]['items'],1)}%)" for m in models) + " |"
    lines.append(row)
    row = "| **Cost per Item** | " + " | ".join(
        f"${meta.get(m,{}).get('cost',0)/max(totals[m]['items'],1):.4f}" for m in models) + " |"
    lines.append(row)
    lines.append("")

    # Per-File Breakdown
    lines.append("---\n")
    lines.append("## Per-File Breakdown\n")

    for file_base in menu_files:
        lines.append(f"### {file_base}\n")
        lines.append(header)
        lines.append(sep)

        analyses = all_analyses[file_base]
        row = "| Restaurant | " + " | ".join(
            analyses[m]["restaurant_name"][:25] for m in models) + " |"
        lines.append(row)
        row = "| Categories | " + " | ".join(
            str(analyses[m]["categories"]) for m in models) + " |"
        lines.append(row)
        row = "| Total Items | " + " | ".join(
            str(analyses[m]["items"]) for m in models) + " |"
        lines.append(row)
        row = "| Items w/ Description | " + " | ".join(
            str(analyses[m]["items_with_description"]) for m in models) + " |"
        lines.append(row)
        row = "| Items w/ Dietary | " + " | ".join(
            str(analyses[m]["items_with_dietary"]) for m in models) + " |"
        lines.append(row)
        row = "| Price Range | " + " | ".join(
            analyses[m]["price_range"] for m in models) + " |"
        lines.append(row)
        row = "| Processing Time | " + " | ".join(
            f"{file_durations.get(m, {}).get(file_base, 0):.1f}s" for m in models) + " |"
        lines.append(row)
        lines.append("")

    # Recommendation (data-driven from run results)
    lines.append("---\n")
    lines.append("## Recommendation\n")

    if meta:
        # Find best model per metric
        best_cost = min(models, key=lambda m: meta.get(m, {}).get("cost", 999))
        best_items = max(models, key=lambda m: totals[m]["items"])
        best_accuracy = max(models, key=lambda m: totals[m]["price"] + totals[m]["dietary"])
        best_speed = min(models, key=lambda m: meta.get(m, {}).get("duration", 999))

        # Cost per item
        cost_per_item = {m: meta.get(m, {}).get("cost", 0) / max(totals[m]["items"], 1) for m in models}
        best_value = min(models, key=lambda m: cost_per_item[m])

        lines.append("**Based on this run's results:**\n")
        lines.append("| Metric | Winner | Value |")
        lines.append("|--------|--------|-------|")
        lines.append(f"| Most items extracted | {best_items} | {totals[best_items]['items']} items |")
        lines.append(f"| Best accuracy (price + dietary) | {best_accuracy} | {totals[best_accuracy]['price']} priced, {totals[best_accuracy]['dietary']} dietary |")
        lines.append(f"| Lowest total cost | {best_cost} | ${meta.get(best_cost, {}).get('cost', 0):.4f} |")
        lines.append(f"| Best cost per item | {best_value} | ${cost_per_item[best_value]:.5f}/item |")
        lines.append(f"| Fastest | {best_speed} | {meta.get(best_speed, {}).get('duration', 0):.1f}s |")
        lines.append("")

        lines.append("**Use case guidance:**\n")
        lines.append("| Use Case | Recommended | Rationale |")
        lines.append("|----------|-------------|-----------|")
        lines.append(f"| Production (accuracy + cost) | {best_value} | Best cost-per-item with strong quality |")
        lines.append(f"| Maximum coverage | {best_items} | Extracts the most items from menus |")
        lines.append(f"| Budget-constrained bulk | {best_cost} | Lowest total cost across all files |")
        lines.append(f"| Speed-critical | {best_speed} | Fastest total processing time |")
        lines.append("")
    else:
        lines.append("*No run metadata available for recommendations.*\n")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Compare batch runs and generate markdown report")
    parser.add_argument("--last", "-l", type=int, default=None,
                        help="Compare only the last N runs (default: all)")
    parser.add_argument("--model", "-m", type=str, default=None,
                        help="Filter runs by model name")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output file path (default: stdout)")
    args = parser.parse_args()

    RUNS = discover_runs()
    if not RUNS:
        print("No run folders found in batch_runs/")
        sys.exit(1)

    if args.model:
        RUNS = {k: v for k, v in RUNS.items() if args.model.lower() in k.lower()}
    if args.last:
        RUNS = dict(list(RUNS.items())[-args.last:])
    if not RUNS:
        print("No matching runs found.")
        sys.exit(1)

    report = generate_report(RUNS)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Report saved to: {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
