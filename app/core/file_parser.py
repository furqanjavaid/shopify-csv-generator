"""Parse client CSV and Excel product files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from app.utils.helpers import clean_value


class FileParser:
    """Parse CSV or Excel files into a normalized dict structure."""

    def parse(self, filepath: str) -> dict[str, Any]:
        path = Path(filepath)
        if not path.exists():
            raise ValueError(f"File not found: {filepath}")

        suffix = path.suffix.lower()
        if suffix == ".csv":
            df = self._read_csv(path)
        elif suffix in {".xlsx", ".xls"}:
            try:
                df = pd.read_excel(path, sheet_name=0, dtype=str)
            except Exception as exc:
                raise ValueError(f"Unable to read Excel file: {exc}") from exc
        else:
            raise ValueError("Unsupported file type. Please use .csv, .xlsx, or .xls")

        if df is None or df.empty:
            raise ValueError("File is empty or has no data rows.")

        df.columns = [clean_value(col) for col in df.columns]
        if not any(df.columns):
            raise ValueError("File has no readable column headers.")

        df = df.fillna("")
        for col in df.columns:
            df[col] = df[col].map(clean_value)

        headers = list(df.columns)
        rows = df.to_dict(orient="records")

        return {
            "headers": headers,
            "rows": rows,
            "row_count": len(rows),
        }

    def _read_csv(self, path: Path) -> pd.DataFrame:
        last_error: Exception | None = None
        for encoding in ("utf-8", "latin-1"):
            try:
                return pd.read_csv(path, dtype=str, encoding=encoding)
            except Exception as exc:
                last_error = exc
        raise ValueError(f"Unable to read CSV file: {last_error}") from last_error
