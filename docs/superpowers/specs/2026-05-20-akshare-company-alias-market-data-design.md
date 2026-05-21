# AkShare Company Alias Enrichment and Market Data Design

## Purpose

OpenKB is becoming an investment research knowledge base, but two gaps reduce
its usefulness:

1. The same listed company can appear under several names and create duplicate
   `companies/`, `concepts/`, or `industries/` pages.
2. The KB cannot currently answer basic market-data questions such as current
   price, PE, PB, market cap, or ETF/fund NAV.

This design intentionally solves those in sequence. The first priority is not
market data; it is company identity. Market data is useful only after company
names and tickers resolve deterministically.

## Decisions From Discussion

- Cover A-share, Hong Kong, and US equities.
- Use AkShare as the collection dependency, behind a provider interface.
- Prefer AkShare functions backed by Xueqiu data. Do not hand-roll direct
  Xueqiu HTTP clients in OpenKB.
- Do not collect full-market quote snapshots by default.
- Do use full-market symbol/name lists as an index for resolving local company
  names.
- Do not enrich noisy company profile fields such as business scope, address,
  website, executives, or company introduction in Phase 1.
- Phase 1 enriches only company aliases and identifiers so generation does not
  split one company into several pages.
- Market data snapshots are Phase 2 and depend on resolved symbols.

## Non-Goals

To prevent scope creep, Phase 1 explicitly does NOT:

- Cover the full A/H/US listed universe. Phase 1 operates only on companies
  that appear in the current KB (registry + `wiki/companies/` + misplaced pages
  + summary-extracted candidates).
- Attempt to merge or rewrite existing `wiki/*.md` pages automatically.
- Introduce Xueqiu cookies or any external credentials in `wiki/` output or API
  responses.
- Replace the existing canonical_id slugs in the registry. Existing ids stay;
  only aliases and identifiers are added.

## Phasing

### Phase 1: Company Alias Enrichment and Duplicate Prevention

Goal: keep one canonical company identity per real company, even when source
documents use old names, short names, English names, legal names, ticker names,
or market-specific variants.

Outputs:

- enriched `.openkb/entity_registry/companies.yaml`
- alias suggestions under `.openkb/entity_registry/resolution/`
- cached symbol indexes under `.openkb/entity_registry/symbol_index/`
- compiler hard rules that block company aliases from becoming concepts or
  industries (extending existing registry integration)
- lint findings for existing duplicate or wrong-namespace pages

### Phase 2: Market Snapshot Layer

Goal: expose price, PE, PB, market cap, valuation, ETF/fund quote, and NAV data
to the UI and query agent through cached snapshots, on demand.

Outputs:

- `.openkb/market_data/current/<symbol>.json`
- API and CLI refresh commands
- query-agent market snapshot tool

Phase 2 must not block Phase 1. Phase 2 is intentionally minimal here; detailed
field set, retention, and TTL policy are deferred to a dedicated Phase 2
follow-up spec written when Phase 1 is stable.

## Scope

### In Scope For Phase 1

- Build and cache symbol/name indexes for A-share, Hong Kong, and US markets
  through the AkShare provider.
- Scan the existing KB for company candidates.
- Resolve candidates to known securities using deterministic matching and
  confidence rules.
- Use AkShare Xueqiu-backed functions only to extract company name aliases and
  identifiers — never to populate business descriptions.
- Write alias suggestions before applying registry changes.
- Apply high-confidence, non-ambiguous alias updates to the entity registry.
- Prevent registered company aliases from creating duplicate `companies/`,
  `concepts/`, or `industries/` pages.

### Out Of Scope For Phase 1

- Full company profile enrichment.
- Business description, address, website, executives, registration capital, or
  long company overview fields.
- Full-market per-company detail crawling.
- PE/PB/price snapshots.
- K-line data, intraday data, backtesting, valuation models, and financial
  statement ingestion.
- Automatic deletion or merging of existing pages without review.

## Data Sources And Provider Abstraction

OpenKB treats AkShare as the dependency boundary. All AkShare access must go
through a thin `MarketDataProvider` interface so that:

- tests can inject a fake provider (no `monkeypatch.setattr("akshare.*", ...)`)
- AkShare function renames or schema drift are localized to one adapter
- future providers (e.g. an alternative open data source) can be swapped in

Suggested interface (Phase 1 surface only; Phase 2 will add quote methods):

```python
class MarketDataProvider(Protocol):
    name: str  # e.g. "akshare:xueqiu"

    def list_symbols(self, market: Literal["CN_A", "HK", "US"]) -> list[SymbolRow]: ...
    def company_aliases(self, symbol: str) -> CompanyAliasInfo: ...
```

Provider priority:

1. `akshare:xueqiu`: AkShare functions backed by Xueqiu pages or Xueqiu data.
2. `akshare:fallback`: non-Xueqiu AkShare functions only when Xueqiu coverage is
   missing.

### AkShare Function Inventory (Phase 1)

Documenting concrete functions so implementation does not under-estimate
normalization effort. AkShare changes field names frequently; the provider
adapter must defensively coerce columns.

| Market | Primary symbol list | Fallback | Alias detail |
|--------|---------------------|----------|--------------|
| CN_A   | `stock_info_a_code_name` (code/name) | `stock_zh_a_spot_em` | `stock_individual_basic_info_xq(symbol="SH600519")` |
| HK     | `stock_hk_spot_em` (代码/名称, Chinese headers) | `stock_hk_famous_spot_em` | `stock_individual_basic_info_hk_xq(symbol="00700")` |
| US     | `stock_us_spot_em` (代码/名称, Chinese headers, slow) | `stock_us_famous_spot_em` | `stock_individual_basic_info_us_xq(symbol="MSFT")` |

Implementation notes:

- HK and US "spot" functions return Chinese column headers; the adapter must
  rename to `symbol/short_name/...` before serializing.
- `stock_individual_basic_info_*_xq` may return varying field sets across
  AkShare versions; the adapter extracts only `org_name_cn / org_short_name_cn
  / org_name_en / org_short_name_en / classi_name / main_operation_business`
  and silently drops unknowns.
- The provider exposes a `function_version` field on every result so failures
  can be attributed to a concrete AkShare call site.

If a future AkShare function requires cookies or external credentials, those
must be local-only and must never be written to `wiki/` or returned through APIs.

## Universe

OpenKB does not collect quote data for the full market by default.

Phase 1 may build full-market symbol indexes because those are lookup tables,
not quote snapshots. The enrichment job then operates on the KB-relevant
universe only:

- companies already in `.openkb/entity_registry/companies.yaml`
- pages in `wiki/companies/*.md`
- company-like pages wrongly created under `wiki/concepts/` or
  `wiki/industries/`
- company names extracted from approved summaries and promotion outputs
- user watchlist entries, if configured later

Only resolved companies are eligible for quote refresh in Phase 2.

## Registry Format

The registry remains the source of truth for identity, not company facts.

### Canonical ID Convention

- Existing canonical_ids are not renamed by Phase 1. New records prefer the
  same style as the existing KB (Chinese short name for CN/HK companies,
  ticker-style for US companies when no common Chinese name exists).
- For a company with multiple listings (A+H, primary+ADR), **use a single
  canonical record** and attach multiple identifiers under it. Cross-listed
  records that already exist as two pages are reported by lint as merge
  candidates but never merged automatically.

### Identifier Schema

Identifiers must align with the existing `_flatten_identifiers` indexing
(`openkb/entity_registry.py:171`). To avoid the prior ambiguity around bare
`xueqiu_symbol: SH601127` strings (which currently get casefolded as a scalar
and bypass exchange indexing), Phase 1 standardizes on:

```yaml
identifiers:
  xueqiu_symbol: SH601127        # scalar shortcut, indexer parses SH/SZ/HK prefix
  tickers:
    - {exchange: SSE,  symbol: "601127"}
    - {exchange: HKEX, symbol: "09927"}  # only if cross-listed
  market: CN_A                   # primary market for the canonical record
```

The `_flatten_identifiers` function must be extended in implementation to
recognize `xueqiu_symbol` as a known key and emit both a raw index entry
(`xueqiu_symbol:SH601127`) and an exchange-aware index entry
(`tickers:exchange_symbol: SSE:601127`) when the prefix can be parsed. This
keeps the existing complex `{exchange, symbol}` ticker index and the new
Xueqiu key consistent.

### Examples

A-share record (single listing):

```yaml
companies:
  赛力斯:
    canonical_name: 赛力斯
    display_name: 赛力斯
    aliases:
      - 小康股份
      - 重庆小康工业集团股份有限公司
      - SERES
    identifiers:
      xueqiu_symbol: SH601127
      market: CN_A
```

Hong Kong record:

```yaml
companies:
  腾讯控股:
    canonical_name: 腾讯控股
    display_name: 腾讯控股
    aliases:
      - 腾讯
      - Tencent
      - Tencent Holdings
      - "0700.HK"
    identifiers:
      xueqiu_symbol: HK00700
      market: HK
```

US record:

```yaml
companies:
  微软:
    canonical_name: Microsoft
    display_name: 微软
    aliases:
      - MSFT
      - Microsoft Corporation
    identifiers:
      xueqiu_symbol: MSFT
      market: US
```

Cross-listed record (A+H):

```yaml
companies:
  中芯国际:
    canonical_name: 中芯国际
    display_name: 中芯国际
    aliases:
      - SMIC
      - Semiconductor Manufacturing International Corporation
    identifiers:
      xueqiu_symbol: SH688981       # primary
      market: CN_A
      tickers:
        - {exchange: SSE,  symbol: "688981"}
        - {exchange: HKEX, symbol: "00981"}
      xueqiu_symbol_alt:
        - HK00981
```

The important rule remains: all aliases resolve to one canonical registry
record and one canonical page path.

## Symbol Index Cache

Full-market symbol/name indexes are cached under the registry namespace (these
are identity lookup tables, not market data):

```text
.openkb/entity_registry/symbol_index/cn_a.json
.openkb/entity_registry/symbol_index/hk.json
.openkb/entity_registry/symbol_index/us.json
```

Each item includes, when available:

```json
{
  "symbol": "SH601127",
  "market": "CN_A",
  "short_name": "赛力斯",
  "full_name": "重庆小康工业集团股份有限公司",
  "english_name": null,
  "source": "akshare:xueqiu",
  "function_version": "stock_info_a_code_name@1.13.x",
  "as_of": "2026-05-20T20:00:00+08:00"
}
```

Index TTL: 14 days default, 30 days max. Refreshes on demand when missing or
stale. Refresh is lazy — a CLI `--refresh-index` flag forces a rebuild.

## Registry Import

AkShare imports write directly into the legacy registry files:

```text
.openkb/entity_registry/companies.yaml
.openkb/entity_registry/industries.yaml
```

Company import rows are merged by existing registry identifiers and aliases
when possible. Manual canonical names are preserved; imports append aliases,
identifiers, and sources. Strict industry import is opt-in and must not import
loose concept/theme boards.

Registry row shape:

```json
{
  "surface": "小康股份",
  "status": "resolved",
  "confidence": 0.98,
  "matched_by": "exact_alias",
  "target": {
    "canonical_id": "赛力斯",
    "canonical_name": "赛力斯",
    "xueqiu_symbol": "SH601127",
    "market": "CN_A"
  },
  "new_aliases": [
    "小康股份",
    "重庆小康工业集团股份有限公司"
  ],
  "source": "akshare:xueqiu"
}
```

Ambiguous shape:

```json
{
  "surface": "中芯国际",
  "status": "ambiguous",
  "matches": [
    {"symbol": "SH688981", "market": "CN_A", "name": "中芯国际"},
    {"symbol": "HK00981",  "market": "HK",  "name": "中芯国际"}
  ],
  "reason": "multiple_listings",
  "suggested_resolution": "single_canonical_with_alt_identifiers"
}
```

Ambiguous records require user review. The system never silently chooses one
listing. The `suggested_resolution` field hints at the cross-listing convention
above so reviewers can apply it consistently.

## Matching Rules

The resolver prefers deterministic matching over fuzzy matching.

Resolution states:

- `resolved`: exactly one high-confidence match.
- `ambiguous`: multiple plausible securities.
- `unresolved`: no reliable match.
- `manual_needed`: a match exists but confidence is below the apply threshold.

High-confidence signals (each with the confidence the resolver will report):

- exact ticker or Xueqiu symbol match → 1.00 (`matched_by=identifier`)
- exact registry alias match → 0.98 (`matched_by=alias`)
- exact short_name match where **only one record across all loaded markets
  exists with that short_name** → 0.97 (`matched_by=symbol_index_short_name`)
- exact full legal name match across all markets, single result → 0.96
  (`matched_by=symbol_index_full_name`)
- legal-suffix-normalized match across all markets, single result → 0.92
  (`matched_by=legal_name`)

Note: a same-name listing in both CN_A and HK (e.g. 中芯国际, 比亚迪) trivially
fails the "single record across all loaded markets" condition and is routed to
`ambiguous`. The earlier "single market result" wording is replaced because
indexes are union-searched, not per-market.

Low-confidence fuzzy matches are suggestions only; they never auto-apply.

### Confidence Thresholds

To avoid coupling enrichment policy to resolver internals:

- The resolver in `openkb/entity_registry.py` returns its own confidence values
  (0.92 / 0.97 / 0.98 / 1.00 as above).
- Enrichment policy reads `auto_apply_min_confidence` (default 0.95) from
  config and applies only suggestions at or above that bar. With the default
  0.95, exact alias and symbol matches auto-apply; legal-suffix matches do not.
- Operators who want stricter behavior raise the threshold; operators who want
  legal-suffix auto-apply lower it to 0.92 explicitly.

## Enrichment Workflow

CLI commands:

```bash
openkb entity import-akshare
openkb entity import-akshare --industries
openkb entity registry-status
```

Process:

1. Load current entity registry.
2. Fetch full symbol rows for A-share, Hong Kong, and US markets.
3. Resolve rows against registry by symbol/identifier and alias.
4. For resolved symbols, call AkShare Xueqiu-backed detail functions only to
   extract alias/name fields. Never write business description or other
   profile noise.
5. Update registry YAML directly. Never overwrite an existing
   `canonical_name`; only append aliases, identifiers, and sources.
6. Commit registry changes through existing KB Git helpers when
   possible. A Git auto-commit failure does not invalidate the enrichment
   result.

API endpoints:

```text
GET  /api/entities/registry
POST /api/entities/import-akshare
```

## Compiler Enforcement

The compiler uses the enriched registry as a hard constraint. The existing
integration points are:

- `_resolve_company_items_against_registry` (`openkb/agent/compiler.py:1407`)
- `_resolve_investment_page_plan_against_registry`
  (`openkb/agent/compiler.py:1522`) — already drops company-alias surfaces
  from industry plans

What is added in Phase 1:

1. **New: concepts plan filter.** Insert a new
   `_resolve_concepts_plan_against_registry` call into the concepts pipeline,
   immediately after concept plan generation and before
   `_dedupe_concept_plan`. It drops any concept candidate whose surface
   resolves to a registered company alias (mirroring the industry plan
   behavior).
2. **Hot-path enrichment for unregistered companies.** When
   `_resolve_company_items_against_registry` cannot match a candidate, the
   compiler additionally checks the cached `symbol_index` for an exact
   short_name / full_name match. If a single match is found across all
   markets, the candidate is recorded in
   `unresolved_companies.json` with `proposed_canonical` for the next
   `--apply` run. The compiler still allows the LLM-proposed page to be
   created (preserving current behavior), but flags it for review. This keeps
   duplicate accumulation bounded without forcing a hard registry dependency.
3. **Canonical context for the prompt.** When a candidate maps to an existing
   canonical company, the canonical display name and page path are passed
   into the prompt context so the LLM writes content under the canonical
   identity.
4. **Existing duplicates produce lint or merge suggestions; they must not be
   deleted automatically.**

This builds on existing `openkb/entity_registry.py` and the compiler registry
integration. Alias enrichment expands registry coverage; compiler enforcement
prevents future duplication.

## Lint And Repair

New lint findings should identify:

- registered company aliases under `wiki/concepts/`
- registered company aliases under `wiki/industries/`
- multiple company pages whose H1 or filename resolves to the same canonical
  company (especially A+H cross-listings already split into two pages)
- company pages without registry identifiers when a high-confidence symbol
  match exists

Repairs are conservative:

- auto-add exact aliases to registry only through `--apply`
- create merge suggestions for duplicate pages
- never delete user-written pages automatically
- never rewrite source documents

## Phase 2 Market Snapshot Design (minimal sketch)

Phase 2 adds market snapshots after alias resolution is stable. This section
intentionally stays minimal — concrete fields, TTLs, retention, and the query
agent integration are deferred to a Phase 2 follow-up spec written when
Phase 1 has actual usage data.

Minimum viable shape:

```text
.openkb/market_data/current/SH601127.json
```

```json
{
  "symbol": "SH601127",
  "market": "CN_A",
  "name": "赛力斯",
  "source": "akshare:xueqiu",
  "as_of": "2026-05-20T20:00:00+08:00",
  "quote": {
    "price": 123.45,
    "change_pct": 1.2,
    "market_cap": 100000000000,
    "pe_ttm": 35.2,
    "pb": 4.1
  }
}
```

Initial CLI surface:

```bash
openkb market refresh --symbol SH601127
openkb market refresh --all-registered
openkb market status
```

Initial query agent tool:

```text
get_market_snapshot(entity_or_symbol)
```

The query agent must cite `source` and `as_of`. Items deliberately deferred
from this design: `expires_at`, `stale` flag semantics, `missing_fields`
reporting, historical retention policy, fund/NAV separate handling, raw
response capture. None of these block Phase 1.

## Configuration

Recommended config shape (Phase 1 keys only; Phase 2 keys land with the
follow-up spec):

```yaml
entity_enrichment:
  enabled: true
  provider_priority:
    - akshare:xueqiu
    - akshare:fallback
  symbol_index_ttl_days: 14
  auto_apply_min_confidence: 0.95
  allow_fuzzy_auto_apply: false
  rate_limit:
    cn_a_requests_per_minute: 30
    hk_requests_per_minute: 10
    us_requests_per_minute: 6
    retry_backoff_seconds: [2, 5, 15]
```

HK and US rate limits are intentionally lower because AkShare's eastmoney /
Xueqiu backends throttle these markets aggressively in practice.

## Error Handling

- AkShare not installed: clear error suggesting the `market` extra (added in
  Phase 1 implementation PR).
- AkShare function missing or changed: provider records the `function_version`
  it tried, marks the call as a provider failure, and continues to fallback
  providers when configured.
- No symbol match: write `unresolved` and do not mutate registry.
- Multiple symbol matches: write `ambiguous` and require review.
- Missing alias fields: keep existing registry aliases and record
  `missing_fields` in suggestion metadata.
- **Delisted / restructured symbol no longer in fresh symbol index**: keep the
  existing registry record and its aliases as-is; add a
  `delisted_observed_at` note in the suggestion file. Aliases are never
  removed automatically — a delisted company's old name still resolves
  historical documents.
- **Xueqiu short_name conflicts with existing canonical_name**: never
  overwrite `canonical_name`. Append the new name only to `aliases` (if not
  already present) and emit a `canonical_name_conflict` review item.
- Git auto-commit failure: record a warning; do not treat successful
  enrichment or review writes as failed.
- Network failure: keep previous cache and mark stale where applicable.

## Testing Strategy

Tests must avoid real AkShare network calls. Mocking style:

- Use the `MarketDataProvider` Protocol. Tests pass a `FakeProvider` instance
  with deterministic in-memory data.
- Do **not** `monkeypatch.setattr("akshare.stock_info_a_code_name", ...)` —
  AkShare attribute paths shift across releases and break tests.
- Provide one provider contract test that, when AkShare is installed, verifies
  the real adapter returns the documented fields. This test is marked
  `@pytest.mark.network` and skipped by default.

Required coverage:

- symbol index normalization for A-share, Hong Kong, and US rows (including
  Chinese column-header coercion for HK/US)
- exact alias matching
- exact ticker and Xueqiu symbol matching (including the new
  `xueqiu_symbol` prefix parsing in `_flatten_identifiers`)
- legal suffix normalization
- ambiguous cross-listing handling (CN_A + HK same short_name)
- dry-run suggestion output
- apply updating registry aliases and identifiers without overwriting
  `canonical_name`
- compiler refusing company aliases as concepts (new concepts plan filter)
- compiler hot-path symbol_index lookup recording `unresolved` candidates
- lint reporting duplicate company pages and wrong namespaces
- delisted-symbol handling preserves aliases
- canonical_name conflict produces review item, not overwrite

## Success Criteria

Phase 1 is successful when:

- KB company candidates can be resolved against AkShare-backed symbol indexes.
- High-confidence aliases can be reviewed and applied into the registry.
- A company with old names or alternate names resolves to one canonical
  company page.
- Registered company aliases cannot create concept or industry pages.
- Cross-listed companies live in one canonical record with multiple
  identifiers.
- Existing duplicate pages are reported for review.
- No company profile noise is written into the registry.
- AkShare is reachable only through `MarketDataProvider`; no `import akshare`
  appears outside the adapter module.

Phase 2 success criteria are defined in the Phase 2 follow-up spec.
