# D-ToolsStreamline

Automation starter for pulling **cost pricing from D-Tools API** for as many line items as possible from an RFP spreadsheet (CSV/XLSX).

## What this does

`scripts/dtools_pricing_from_rfp.py`:
- Reads your RFP file.
- Detects key fields (manufacturer, part number, description, quantity) using `config/column_aliases.json`.
- Searches D-Tools catalog for each line item.
- Pulls pricing for matched items.
- Outputs a line-by-line results CSV with match status and cost.
- Prints coverage and total estimated cost.

## Authentication required by D-Tools Cloud API

This script is wired to the required D-Tools auth model:

1. **Basic Authorization** (fixed, required value):
   - `Authorization: Basic RFRDbG91ZEFQSVVzZXI6MyNRdVkrMkR1QCV3Kk15JTU8Yi1aZzlV`
2. **API Key**:
   - `X-API-Key: <your key>` via `DTOOLS_API_KEY`.

If either is missing/invalid, D-Tools returns `401 Unauthorized`.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your API base URL and (if needed) endpoint paths.

## Run

```bash
python scripts/dtools_pricing_from_rfp.py \
  --rfp /path/to/your/rfp.xlsx \
  --sheet "Sheet1" \
  --output output/rfp_pricing_results.csv
```

For CSV input, omit `--sheet`.

## Output

The script writes a CSV with:
- Source row and original identifying fields.
- Match state (`priced`, `unmatched`, `matched_no_cost`, `api_error`, etc.).
- Matched D-Tools item info.
- `unit_cost` and `extended_cost` when available.

At the end, it prints:
- Items scanned.
- Items priced + pricing coverage percentage.
- Total estimated cost.

## Tuning for your RFP format

If your RFP uses different column names, update `config/column_aliases.json`.

Example mappings:
- `"part #"`, `"sku"`, `"model #"` -> `part_number`
- `"qty"`, `"units"` -> `quantity`

## Notes

- This workflow is designed to maximize “priced” coverage even when some items fail or are missing in catalog.
- Endpoint response fields can vary by account/version; cost extraction checks several common fields (`cost`, `dealerCost`, `unitCost`, `price`, tiered costs).
