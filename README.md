# Shopify CSV Generator

Desktop app that converts product CSV/Excel files or product listing URLs into Shopify-ready import CSV.

## Features

- **Upload File** — CSV or Excel with auto column mapping
- **Scrape URL** — JSON-LD / Open Graph / HTML heuristics
- **Shopify CSV** — Exact 36-field product import format

## First time setup

```bash
pip install -r requirements.txt
```

## Run in development

```bash
python main.py
```

## Build executable

```bash
python build.py
```

Output will be in `/dist/` folder:

- Windows: `ShopifyCSVGenerator.exe`
- Mac: `ShopifyCSVGenerator` (run with open or double-click)

## Notes

- Build on Windows to get `.exe`, build on Mac to get `.app`
- Do not share the `/build/` or `/dist/` folders — rebuild on each machine
- Window size: 900×650, dark theme (CustomTkinter)

## Tech stack

Python 3.11 · CustomTkinter · pandas · BeautifulSoup4 · PyInstaller
