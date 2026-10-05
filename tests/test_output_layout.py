"""Tests for domain folder + prefixed artifact naming."""

from __future__ import annotations

from pathlib import Path


def test_domain_folder_name_uses_first_label():
    from sentivo_extractor.core.output_layout import domain_folder_name

    assert domain_folder_name("https://www.directplastics.co.uk/acetal-rod") == (
        "directplastics"
    )
    assert domain_folder_name("https://shop.example.com/p") == "shop"
    assert domain_folder_name("directplastics.co.uk") == "directplastics"


def test_domain_artifact_path_prefixes_filename(tmp_path: Path):
    from sentivo_extractor.core.output_layout import domain_artifact_path

    path = domain_artifact_path(tmp_path, "directplastics", "shopify_import.csv")
    assert path == tmp_path / "directplastics" / "directplastics_shopify_import.csv"
    assert path.name == "directplastics_shopify_import.csv"
