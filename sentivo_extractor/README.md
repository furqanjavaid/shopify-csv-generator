# Sentivo Universal Product Extractor

Modular pipeline: **audit → pilot QA → full extract**.

## Pilot (recommended before mass extract)

```bash
python -m sentivo_extractor extract \
  --input input/hashim_websites.csv \
  --output output/pilot/ \
  --use-playwright true \
  --pilot true \
  --pilot-size-per-domain 20 \
  --check-image-urls true \
  --qa-sample-size-per-domain 20 \
  --duplicate-sku-policy warn \
  --min-coverage-percent 90
```

## Full extraction

```bash
python -m sentivo_extractor extract \
  --input input/hashim_websites.csv \
  --output output/ \
  --use-playwright true \
  --max-products-per-domain 500 \
  --check-image-urls true
```

## Audit

```bash
python -m sentivo_extractor audit --input input/hashim_websites.csv --output output/audit/
```

## QA outputs

| File | Purpose |
|------|---------|
| `qa/sample_review.xlsx` | Manual review + `approved` / `manual_review_notes` |
| `shopify_pre_import_validation.xlsx` | Shopify CSV structural checks |
| `production_summary.xlsx` | Overview, coverage, errors, yellow/red |
| `run_summary.json` | Machine-readable summary |

GUI path unchanged: `python main.py` (no CLI flags).
