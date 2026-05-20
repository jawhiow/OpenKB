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
- Use AkShare as the collection dependency.
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

## Phasing

### Phase 1: Company Alias Enrichment and Duplicate Prevention

Goal: keep one canonical company identity per real company, even when source
documents use old names, short names, English names, legal names, ticker names,
or market-specific variants.

Outputs:

- enriched `.openkb/entity_registry/companies.yaml`
- alias suggestions under `.openkb/market_data/resolution/`
- compiler hard rules that block company aliases from becoming concepts or
  industries
- lint findings for existing duplicate or wrong-namespace pages

### Phase 2: Market Snapshot Layer

Goal: expose price, PE, PB, market cap, valuation, ETF/fund quote, and NAV data
to the UI and query agent through cached snapshots.

Outputs:

- `.openkb/market_data/current/<symbol>.json`
- `.openkb/market_data/history/<symbol>/<date>.json`
- API and CLI refresh commands
- query-agent market snapshot tool

Phase 2 must not block Phase 1.

## Scope

### In Scope For Phase 1

- Build and cache symbol/name indexes for A-share, Hong Kong, and US markets
  through AkShare.
- Scan the existing KB for company candidates.
- Resolve candidates to known securities using deterministic matching and
  confidence rules.
- Use AkShare Xueqiu-backed functions, such as
  `stock_individual_basic_info_xq(symbol="SH601127")`, only to extract
  company name aliases and identifiers.
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

## Data Sources

OpenKB should treat AkShare as the dependency boundary. The provider should call
AkShare functions and normalize outputs into OpenKB schemas.

Provider priority:

1. `akshare:xueqiu`: AkShare functions backed by Xueqiu pages or Xueqiu data.
2. `akshare:fallback`: non-Xueqiu AkShare functions only when Xueqiu coverage is
   missing.

OpenKB should not directly scrape Xueqiu or maintain Xueqiu cookies in Phase 1.
If a future AkShare function requires cookies or external credentials, those
must be local-only and must never be written to `wiki/` or returned through APIs.

## Universe

OpenKB should not collect quote data for the full market by default.

Phase 1 may build full-market symbol indexes because those are lookup tables,
not quote snapshots. The enrichment job then operates on the KB-relevant
universe:

- companies already in `.openkb/entity_registry/companies.yaml`
- pages in `wiki/companies/*.md`
- company-like pages wrongly created under `wiki/concepts/` or
  `wiki/industries/`
- company names extracted from approved summaries and promotion outputs
- user watchlist entries, if configured later

Only resolved companies should be eligible for quote refresh in Phase 2.

## Registry Format

The registry remains the source of truth for identity, not company facts.

Example A-share record:

```yaml
companies:
  SERES:
    canonical_name: 赛力斯
    display_name: 赛力斯
    aliases:
      - 赛力斯
      - 小康股份
      - 重庆小康工业集团股份有限公司
    identifiers:
      xueqiu_symbol: SH601127
      market: CN_A
```

Example Hong Kong record:

```yaml
companies:
  Tencent:
    canonical_name: 腾讯控股
    display_name: 腾讯控股
    aliases:
      - 腾讯
      - 腾讯控股
      - Tencent
      - Tencent Holdings
      - 0700.HK
    identifiers:
      xueqiu_symbol: HK00700
      market: HK
```

Example US record:

```yaml
companies:
  Microsoft:
    canonical_name: Microsoft
    display_name: 微软
    aliases:
      - MSFT
      - 微软
      - Microsoft
    identifiers:
      xueqiu_symbol: MSFT
      market: US
```

Canonical IDs can keep the repo's existing style. The important rule is that
all aliases resolve to one canonical registry record and one canonical page
path.

## Symbol Index Cache

Full-market symbol/name indexes are cached for matching:

```text
.openkb/market_data/symbol_index/cn_a.json
.openkb/market_data/symbol_index/hk.json
.openkb/market_data/symbol_index/us.json
```

Each item should include, when available:

```json
{
  "symbol": "SH601127",
  "market": "CN_A",
  "short_name": "赛力斯",
  "full_name": "重庆小康工业集团股份有限公司",
  "english_name": null,
  "source": "akshare:xueqiu",
  "as_of": "2026-05-20T20:00:00+08:00"
}
```

Index TTL: 7 to 30 days. It should refresh on demand when missing or stale.

## Alias Suggestion Files

Enrichment writes suggestions before mutating registry files:

```text
.openkb/market_data/resolution/company_alias_suggestions.json
.openkb/market_data/resolution/unresolved_companies.json
.openkb/market_data/resolution/ambiguous_companies.json
```

Suggestion shape:

```json
{
  "surface": "小康股份",
  "status": "resolved",
  "confidence": 0.98,
  "matched_by": "exact_alias",
  "target": {
    "canonical_id": "SERES",
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
    {"symbol": "HK00981", "market": "HK", "name": "中芯国际"}
  ],
  "reason": "multiple_listings"
}
```

Ambiguous records require user review. The system must not silently choose one
listing.

## Matching Rules

The resolver should prefer deterministic matching over fuzzy matching.

Resolution states:

- `resolved`: exactly one high-confidence match.
- `ambiguous`: multiple plausible securities.
- `unresolved`: no reliable match.
- `manual_needed`: a match exists but confidence is below the apply threshold.

High-confidence signals:

- exact ticker or Xueqiu symbol match
- exact registry alias match
- exact symbol-index short name match with a single market result
- exact full legal name match
- legal suffix normalized match, only when it returns one result

Low-confidence fuzzy matches should be suggestions only. They must not auto-apply.

Multiple listings should be explicit. A company may legitimately have both
A-share and Hong Kong listings, but that relationship should be encoded in one
canonical company record only after user review.

## Enrichment Workflow

CLI commands:

```bash
openkb entity enrich-aliases --dry-run
openkb entity enrich-aliases --apply
openkb entity alias-status
```

Process:

1. Load current entity registry.
2. Build or load symbol indexes for A-share, Hong Kong, and US markets.
3. Collect KB company candidates.
4. Resolve candidates against registry and symbol indexes.
5. For resolved symbols, call AkShare Xueqiu-backed detail functions only to
   extract alias/name fields.
6. Write suggestion files.
7. If `--apply` is set, update registry only for high-confidence, non-ambiguous
   suggestions.
8. Commit registry and suggestion changes through existing KB Git helpers when
   possible. A Git auto-commit failure should not invalidate the enrichment
   result.

API endpoints can be added after the CLI is stable:

```text
POST /api/entities/enrich-aliases
GET  /api/entities/alias-suggestions
POST /api/entities/alias-suggestions/apply
```

## Compiler Enforcement

The compiler must use the enriched registry as a hard constraint.

Rules:

1. If an extracted company candidate matches a registered alias, write or update
   the canonical company page path instead of creating a new page.
2. If an extracted concept candidate matches a registered company alias, drop it
   from concept creation.
3. If an extracted industry candidate matches a registered company alias, drop it
   from industry creation.
4. If an extracted company candidate maps to an existing canonical company, pass
   the canonical display name and path into the prompt context so the LLM writes
   content under the canonical identity.
5. Existing duplicate pages should produce lint or merge suggestions; they must
   not be deleted automatically.

This builds on the existing `openkb/entity_registry.py` and compiler registry
integration. Alias enrichment expands registry coverage; compiler enforcement
prevents future duplication.

## Lint And Repair

New lint findings should identify:

- registered company aliases under `wiki/concepts/`
- registered company aliases under `wiki/industries/`
- multiple company pages whose H1 or filename resolves to the same canonical
  company
- company pages without registry identifiers when a high-confidence symbol match
  exists

Repairs should be conservative:

- auto-add exact aliases to registry only through `--apply`
- create merge suggestions for duplicate pages
- never delete user-written pages automatically
- never rewrite source documents

## Phase 2 Market Snapshot Design

Phase 2 adds market snapshots after alias resolution is stable.

Supported fields, when available:

- price
- change percent
- market cap
- PE TTM
- PB
- dividend yield
- fund or ETF NAV
- currency
- source
- as-of timestamp

Snapshot storage:

```text
.openkb/market_data/current/SH601127.json
.openkb/market_data/history/SH601127/2026-05-20.json
```

Snapshot shape:

```json
{
  "symbol": "SH601127",
  "market": "CN_A",
  "name": "赛力斯",
  "source": "akshare:xueqiu",
  "as_of": "2026-05-20T20:00:00+08:00",
  "expires_at": "2026-05-20T20:30:00+08:00",
  "stale": false,
  "quote": {
    "price": 123.45,
    "change_pct": 1.2,
    "market_cap": 100000000000,
    "pe_ttm": 35.2,
    "pb": 4.1,
    "dividend_yield": null
  },
  "missing_fields": ["dividend_yield"]
}
```

Default refresh policy:

- Quote and valuation TTL: 30 minutes.
- Company alias index TTL: 7 to 30 days.
- Fund NAV TTL: 24 hours.
- Stale snapshots usable for 7 days when refresh fails.
- Historical retention: daily snapshots for 90 days.
- Raw API responses are not kept by default.

Market snapshot commands:

```bash
openkb market refresh --symbol SH601127
openkb market refresh --all-registered
openkb market status
```

Query agent tool:

```text
get_market_snapshot(entity_or_symbol)
```

The query agent must cite `source` and `as_of`. If a snapshot is stale, the
answer must explicitly state that the market data is stale.

## Configuration

Recommended config shape:

```yaml
entity_enrichment:
  enabled: true
  provider_priority:
    - akshare:xueqiu
    - akshare:fallback
  symbol_index_ttl_days: 14
  auto_apply_min_confidence: 0.98
  allow_fuzzy_auto_apply: false

market_data:
  enabled: true
  provider_priority:
    - akshare:xueqiu
    - akshare:fallback
  refresh:
    quote_ttl_minutes: 30
    fund_nav_ttl_hours: 24
    stale_usable_days: 7
  retention:
    history_days: 90
    history_granularity: daily
    keep_raw_responses: false
  rate_limit:
    requests_per_minute: 30
    batch_size: 20
```

## Error Handling

- AkShare not installed: show a clear error and suggest installing the market
  extra once that extra exists.
- AkShare function missing or changed: mark provider failure with the function
  name and continue to fallback providers when configured.
- No symbol match: write `unresolved` and do not mutate registry.
- Multiple symbol matches: write `ambiguous` and require review.
- Missing alias fields: keep existing registry aliases and record
  `missing_fields` in suggestion metadata.
- Git auto-commit failure: record a warning; do not treat successful enrichment
  or review writes as failed.
- Network failure: keep previous cache and mark stale where applicable.

## Testing Strategy

Tests should avoid real AkShare network calls.

Required coverage:

- symbol index normalization for A-share, Hong Kong, and US rows
- exact alias matching
- exact ticker and Xueqiu symbol matching
- legal suffix normalization
- ambiguous multi-listing handling
- dry-run suggestion output
- apply updating registry aliases and identifiers
- compiler refusing company aliases as concepts
- lint reporting duplicate company pages and wrong namespaces
- market snapshot stale handling in Phase 2

## Success Criteria

Phase 1 is successful when:

- KB company candidates can be resolved against AkShare-backed symbol indexes.
- High-confidence aliases can be reviewed and applied into the registry.
- A company with old names or alternate names resolves to one canonical company
  page.
- Registered company aliases cannot create concept or industry pages.
- Existing duplicate pages are reported for review.
- No company profile noise is written into the registry.

Phase 2 is successful when:

- resolved companies can refresh price, PE, PB, market cap, and valuation
  snapshots when the provider exposes those fields.
- snapshots include source and as-of metadata.
- stale data is clearly marked.
- query answers can combine wiki research evidence with market snapshots without
  hiding data freshness.

