#!/usr/bin/env python3
"""Pull D-Tools pricing for as many RFP items as possible.

Usage:
  python scripts/dtools_pricing_from_rfp.py --rfp path/to/rfp.xlsx
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv

REQUIRED_BASIC_AUTH = "Basic RFRDbG91ZEFQSVVzZXI6MyNRdVkrMkR1QCV3Kk15JTU8Yi1aZzlV"


@dataclass
class ItemRequest:
    source_row: int
    manufacturer: str | None
    part_number: str | None
    description: str | None
    quantity: float
    raw: dict[str, Any]


class DToolsApi:
    def __init__(self, base_url: str, api_key: str, search_path: str, pricing_path: str, timeout_seconds: int = 20):
        self.base_url = base_url.rstrip("/")
        self.search_path = search_path
        self.pricing_path = pricing_path
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": REQUIRED_BASIC_AUTH,
                "X-API-Key": api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}{path}"

    def search_item(self, item: ItemRequest) -> dict[str, Any] | None:
        payload = {
            "manufacturer": item.manufacturer,
            "partNumber": item.part_number,
            "description": item.description,
            "limit": 5,
        }
        response = self.session.post(self._url(self.search_path), json=payload, timeout=self.timeout_seconds)
        response.raise_for_status()
        body = response.json()
        results = body.get("items") or body.get("results") or []
        if not results:
            return None

        for candidate in results:
            cpn = str(candidate.get("partNumber") or candidate.get("sku") or "").strip().lower()
            if item.part_number and cpn == item.part_number.strip().lower():
                return candidate
        return results[0]

    def get_pricing(self, item_id: str, currency: str = "USD") -> dict[str, Any]:
        path = self.pricing_path.format(item_id=item_id)
        response = self.session.get(self._url(path), params={"currency": currency}, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()


def load_aliases(path: Path) -> dict[str, list[str]]:
    with path.open("r", encoding="utf-8") as handle:
        data: dict[str, list[str]] = json.load(handle)
    return {k: [normalize(c) for c in v] for k, v in data.items()}


def normalize(value: Any) -> str:
    return str(value).strip().lower().replace("_", " ")


def find_column(df: pd.DataFrame, aliases: list[str]) -> str | None:
    alias_set = set(aliases)
    for col in df.columns:
        if normalize(col) in alias_set:
            return col
    return None


def read_rfp(path: Path, sheet_name: str | None) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_excel(path, sheet_name=sheet_name)


def to_item_requests(df: pd.DataFrame, aliases: dict[str, list[str]]) -> list[ItemRequest]:
    cols = {key: find_column(df, alias_list) for key, alias_list in aliases.items()}
    required = ["part_number", "description"]
    if all(cols[r] is None for r in required):
        raise ValueError(
            "Could not identify part_number or description columns. "
            "Update config/column_aliases.json for your RFP format."
        )

    items: list[ItemRequest] = []
    for idx, row in df.iterrows():
        qty_value = row.get(cols["quantity"]) if cols.get("quantity") else 1
        try:
            quantity = float(qty_value) if pd.notna(qty_value) else 1.0
        except (TypeError, ValueError):
            quantity = 1.0

        item = ItemRequest(
            source_row=idx + 2,
            manufacturer=str(row.get(cols["manufacturer"], "")).strip() or None if cols.get("manufacturer") else None,
            part_number=str(row.get(cols["part_number"], "")).strip() or None if cols.get("part_number") else None,
            description=str(row.get(cols["description"], "")).strip() or None if cols.get("description") else None,
            quantity=quantity,
            raw={str(k): row[k] for k in df.columns},
        )
        if item.part_number or item.description:
            items.append(item)
    return items


def pick_cost(pricing_response: dict[str, Any]) -> float | None:
    for key in ["cost", "dealerCost", "unitCost", "price"]:
        value = pricing_response.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    tiers = pricing_response.get("tiers")
    if isinstance(tiers, list) and tiers:
        for tier in tiers:
            value = tier.get("cost")
            if isinstance(value, (int, float)):
                return float(value)
    return None


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Pull D-Tools pricing for an RFP file.")
    parser.add_argument("--rfp", required=True, type=Path, help="Input CSV/XLSX RFP file")
    parser.add_argument("--sheet", default=None, help="Excel sheet name (optional)")
    parser.add_argument("--output", type=Path, default=Path("output/rfp_pricing_results.csv"))
    parser.add_argument("--aliases", type=Path, default=Path("config/column_aliases.json"))
    parser.add_argument("--currency", default=os.getenv("DTOOLS_CURRENCY", "USD"))
    args = parser.parse_args()

    base_url = os.getenv("DTOOLS_BASE_URL")
    api_key = os.getenv("DTOOLS_API_KEY")
    search_path = os.getenv("DTOOLS_SEARCH_PATH", "/cloud/api/v1/catalog/items/search")
    pricing_path = os.getenv("DTOOLS_PRICING_PATH", "/cloud/api/v1/catalog/items/{item_id}/pricing")
    timeout = int(os.getenv("DTOOLS_TIMEOUT_SECONDS", "20"))

    if not base_url or not api_key:
        raise SystemExit("Missing DTOOLS_BASE_URL or DTOOLS_API_KEY in environment.")

    df = read_rfp(args.rfp, args.sheet)
    aliases = load_aliases(args.aliases)
    items = to_item_requests(df, aliases)

    api = DToolsApi(base_url, api_key, search_path, pricing_path, timeout_seconds=timeout)

    rows: list[dict[str, Any]] = []
    matched = 0
    total_estimated_cost = 0.0

    for item in items:
        status = "unmatched"
        message = "No catalog hit"
        dtools_item_id = None
        matched_part = None
        unit_cost = None
        extended_cost = None

        try:
            found = api.search_item(item)
            if found:
                dtools_item_id = str(found.get("id") or found.get("itemId") or "")
                matched_part = found.get("partNumber") or found.get("sku")
                if dtools_item_id:
                    pricing = api.get_pricing(dtools_item_id, currency=args.currency)
                    unit_cost = pick_cost(pricing)
                    if unit_cost is not None:
                        extended_cost = unit_cost * item.quantity
                        total_estimated_cost += extended_cost
                        matched += 1
                        status = "priced"
                        message = "OK"
                    else:
                        status = "matched_no_cost"
                        message = "Item matched but no usable cost field in pricing response"
                else:
                    status = "matched_no_id"
                    message = "Matched result missing ID"
        except requests.HTTPError as exc:
            status = "api_error"
            message = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        except requests.RequestException as exc:
            status = "network_error"
            message = str(exc)
        except Exception as exc:  # noqa: BLE001
            status = "error"
            message = str(exc)

        rows.append(
            {
                "source_row": item.source_row,
                "manufacturer": item.manufacturer,
                "part_number": item.part_number,
                "description": item.description,
                "quantity": item.quantity,
                "status": status,
                "message": message,
                "dtools_item_id": dtools_item_id,
                "matched_part_number": matched_part,
                "unit_cost": unit_cost,
                "extended_cost": extended_cost,
            }
        )

    out_df = pd.DataFrame(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(args.output, index=False)

    coverage = (matched / len(items) * 100) if items else 0
    print(f"Saved: {args.output}")
    print(f"Items scanned: {len(items)}")
    print(f"Items priced: {matched} ({coverage:.1f}% coverage)")
    print(f"Estimated total cost ({args.currency}): {total_estimated_cost:,.2f}")


if __name__ == "__main__":
    main()
