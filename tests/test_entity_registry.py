from __future__ import annotations

from pathlib import Path

from openkb.entity_registry import EntityRegistry


def _write_registry(
    kb_dir: Path,
    *,
    companies: str = "companies: {}\n",
    industries: str = "industries: {}\n",
) -> None:
    registry_dir = kb_dir / ".openkb" / "entity_registry"
    registry_dir.mkdir(parents=True)
    (registry_dir / "companies.yaml").write_text(companies, encoding="utf-8")
    (registry_dir / "industries.yaml").write_text(industries, encoding="utf-8")


def test_resolves_company_alias_to_canonical_path(tmp_path: Path):
    _write_registry(
        tmp_path,
        companies=(
            "companies:\n"
            "  tencent-holdings:\n"
            "    canonical_name: 腾讯控股有限公司\n"
            "    display_name: 腾讯控股\n"
            "    aliases:\n"
            "      - 腾讯控股\n"
            "      - 腾讯控股有限公司\n"
            "      - Tencent Holdings\n"
            "      - 0700.HK\n"
        ),
    )

    registry = EntityRegistry.load(tmp_path)
    resolved = registry.resolve("腾讯控股", namespace_hint="company")

    assert resolved is not None
    assert resolved.entity_type == "company"
    assert resolved.canonical_id == "tencent-holdings"
    assert resolved.canonical_name == "腾讯控股有限公司"
    assert resolved.path == "companies/tencent-holdings"
    assert resolved.matched_by == "alias"


def test_resolves_industry_alias_to_canonical_path(tmp_path: Path):
    _write_registry(
        tmp_path,
        industries=(
            "industries:\n"
            "  online-advertising:\n"
            "    canonical_name: 在线广告\n"
            "    display_name: 在线广告\n"
            "    aliases:\n"
            "      - 在线广告\n"
            "      - 互联网广告\n"
            "      - Online Advertising\n"
        ),
    )

    registry = EntityRegistry.load(tmp_path)
    resolved = registry.resolve("互联网广告", namespace_hint="industry")

    assert resolved is not None
    assert resolved.entity_type == "industry"
    assert resolved.canonical_id == "online-advertising"
    assert resolved.path == "industries/online-advertising"


def test_resolves_ticker_identifier_when_supplied(tmp_path: Path):
    _write_registry(
        tmp_path,
        companies=(
            "companies:\n"
            "  tencent-holdings:\n"
            "    canonical_name: 腾讯控股有限公司\n"
            "    display_name: 腾讯控股\n"
            "    aliases: [腾讯控股]\n"
            "    identifiers:\n"
            "      ticker:\n"
            "        - exchange: HKEX\n"
            "          symbol: '700'\n"
        ),
    )

    registry = EntityRegistry.load(tmp_path)
    resolved = registry.resolve(
        "Tencent",
        namespace_hint="company",
        identifiers={"ticker": [{"exchange": "HKEX", "symbol": "700"}]},
    )

    assert resolved is not None
    assert resolved.canonical_id == "tencent-holdings"
    assert resolved.matched_by == "identifier"
    assert resolved.confidence == 1.0


def test_namespace_hint_prevents_company_alias_from_matching_industry(tmp_path: Path):
    _write_registry(
        tmp_path,
        companies=(
            "companies:\n"
            "  tencent-holdings:\n"
            "    canonical_name: 腾讯控股有限公司\n"
            "    display_name: 腾讯控股\n"
            "    aliases: [腾讯控股]\n"
        ),
    )

    registry = EntityRegistry.load(tmp_path)

    assert registry.resolve("腾讯控股", namespace_hint="industry") is None


def test_missing_registry_files_behave_as_empty_registry(tmp_path: Path):
    registry = EntityRegistry.load(tmp_path)

    assert registry.records == []
    assert registry.resolve("腾讯控股", namespace_hint="company") is None
