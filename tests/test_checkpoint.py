"""Tests for checkpoint reset / skip reasons."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_checkpoint_reset_clears_file_and_memory():
    from sentivo_extractor.core.checkpoint import CheckpointStore

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "checkpoint.json"
        store = CheckpointStore(path)
        store.mark_done("https://a.example/p/1", {"title": "A"})
        store.mark_failed("https://a.example/p/2", "oops")
        assert path.exists()
        assert store.completed_count() == 1
        assert "https://a.example/p/2" in store.failed

        store.reset()
        assert not path.exists()
        assert store.completed_count() == 0
        assert store.failed == set()
        assert store.products() == []


def test_overwrite_does_not_load_existing_checkpoint():
    from sentivo_extractor.core.crawler import UniversalCrawler
    from sentivo_extractor.core.utils import close_logger

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ck = out / "checkpoint.json"
        ck.write_text(
            json.dumps(
                {
                    "completed_urls": ["https://shop.example/products/old"],
                    "failed_urls": [{"url": "https://shop.example/products/bad", "error": "x"}],
                    "products": [{"title": "Old", "source_url": "https://shop.example/products/old"}],
                }
            ),
            encoding="utf-8",
        )
        crawler = UniversalCrawler(
            {
                "output": str(out),
                "overwrite": True,
                "respect_robots": False,
                "delay": 0,
                "pilot": False,
            }
        )
        try:
            assert crawler.checkpoint.completed_count() == 0
            assert crawler.checkpoint.products() == []
            assert not ck.exists()
        finally:
            close_logger(crawler.logger)


def test_checkpoint_store_load_existing_false():
    from sentivo_extractor.core.checkpoint import CheckpointStore

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "checkpoint.json"
        path.write_text(
            json.dumps({"completed_urls": ["https://x/p"], "failed_urls": [], "products": []}),
            encoding="utf-8",
        )
        store = CheckpointStore(path, load_existing=False)
        assert store.completed_count() == 0


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"[PASS] {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {exc}")
            import traceback

            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
