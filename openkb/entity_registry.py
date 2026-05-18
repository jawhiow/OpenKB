from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import re
import unicodedata

import yaml


ENTITY_TYPES = ("company", "industry")


@dataclass(frozen=True)
class EntityRecord:
    entity_type: str
    canonical_id: str
    canonical_name: str
    display_name: str
    aliases: tuple[str, ...] = ()
    identifiers: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> str:
        namespace = "companies" if self.entity_type == "company" else "industries"
        return f"{namespace}/{self.canonical_id}"

    def all_names(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in (self.canonical_id, self.canonical_name, self.display_name, *self.aliases)
            if value
        )


@dataclass(frozen=True)
class ResolvedEntity:
    entity_type: str
    canonical_id: str
    canonical_name: str
    display_name: str
    path: str
    matched_surface: str
    matched_by: str
    confidence: float


_PUNCT_RE = re.compile(r"[\s\-_\.,，。:：/\\()（）\[\]【】]+")
_LEGAL_SUFFIX_RE = re.compile(
    r"(?:股份有限公司|有限责任公司|控股有限公司|有限公司|集团|控股|公司|"
    r"incorporated|inc\.?|corporation|corp\.?|limited|ltd\.?|llc|plc|holdings?|group)$",
    re.IGNORECASE,
)


def alias_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold().strip()
    return _PUNCT_RE.sub("", text)


def legal_name_key(value: str) -> str:
    key = alias_key(value)
    previous = None
    while key and key != previous:
        previous = key
        key = _LEGAL_SUFFIX_RE.sub("", key).strip()
    return key


class EntityRegistry:
    def __init__(self, records: list[EntityRecord] | None = None):
        self.records = records or []
        self._alias_index: dict[tuple[str, str], EntityRecord] = {}
        self._legal_index: dict[tuple[str, str], EntityRecord] = {}
        self._identifier_index: dict[tuple[str, str, str], EntityRecord] = {}
        for record in self.records:
            self._index(record)

    @classmethod
    def load(cls, kb_dir: Path) -> EntityRegistry:
        root = kb_dir / ".openkb" / "entity_registry"
        records: list[EntityRecord] = []
        records.extend(_load_records(root / "companies.yaml", "companies", "company"))
        records.extend(_load_records(root / "industries.yaml", "industries", "industry"))
        return cls(records)

    def resolve(
        self,
        surface: str,
        *,
        namespace_hint: str = "",
        identifiers: dict[str, Any] | None = None,
    ) -> ResolvedEntity | None:
        namespaces = _namespace_order(namespace_hint)
        if identifiers:
            for namespace in namespaces:
                for key, value in _flatten_identifiers(identifiers):
                    record = self._identifier_index.get((namespace, key, value))
                    if record is not None:
                        return _resolved(record, surface, "identifier", 1.0)

        key = alias_key(surface)
        for namespace in namespaces:
            record = self._alias_index.get((namespace, key))
            if record is not None:
                return _resolved(record, surface, "alias", 0.98)

        key = legal_name_key(surface)
        for namespace in namespaces:
            record = self._legal_index.get((namespace, key))
            if record is not None:
                return _resolved(record, surface, "legal_name", 0.92)
        return None

    def _index(self, record: EntityRecord) -> None:
        for name in record.all_names():
            key = alias_key(name)
            if key:
                self._alias_index[(record.entity_type, key)] = record
            legal_key = legal_name_key(name)
            if legal_key:
                self._legal_index[(record.entity_type, legal_key)] = record
        for key, value in _flatten_identifiers(record.identifiers):
            self._identifier_index[(record.entity_type, key, value)] = record


def _load_records(path: Path, root_key: str, entity_type: str) -> list[EntityRecord]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_records = data.get(root_key) or {}
    if not isinstance(raw_records, dict):
        return []
    records: list[EntityRecord] = []
    for canonical_id, raw in raw_records.items():
        if not isinstance(raw, dict):
            continue
        canonical_name = str(raw.get("canonical_name") or canonical_id).strip()
        display_name = str(raw.get("display_name") or canonical_name).strip()
        aliases = tuple(
            str(item).strip()
            for item in raw.get("aliases") or []
            if str(item).strip()
        )
        identifiers = raw.get("identifiers") or raw.get("external_ids") or {}
        if not isinstance(identifiers, dict):
            identifiers = {}
        records.append(
            EntityRecord(
                entity_type=entity_type,
                canonical_id=str(canonical_id).strip(),
                canonical_name=canonical_name,
                display_name=display_name,
                aliases=aliases,
                identifiers=identifiers,
            )
        )
    return records


def _namespace_order(namespace_hint: str) -> tuple[str, ...]:
    hint = (namespace_hint or "").strip().lower()
    if hint in {"company", "companies"}:
        return ("company",)
    if hint in {"industry", "industries"}:
        return ("industry",)
    return ENTITY_TYPES


def _flatten_identifiers(identifiers: dict[str, Any]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key, value in identifiers.items():
        key_text = str(key).strip().lower()
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict):
                    exchange = str(entry.get("exchange") or "").strip().upper()
                    symbol = str(entry.get("symbol") or entry.get("ticker") or "").strip().upper()
                    if exchange and symbol:
                        items.append((f"{key_text}:exchange_symbol", f"{exchange}:{symbol}"))
                elif str(entry).strip():
                    items.append((key_text, str(entry).strip().casefold()))
        elif isinstance(value, dict):
            exchange = str(value.get("exchange") or "").strip().upper()
            symbol = str(value.get("symbol") or value.get("ticker") or "").strip().upper()
            if exchange and symbol:
                items.append((f"{key_text}:exchange_symbol", f"{exchange}:{symbol}"))
        elif value is not None and str(value).strip():
            items.append((key_text, str(value).strip().casefold()))
    return items


def _resolved(record: EntityRecord, surface: str, matched_by: str, confidence: float) -> ResolvedEntity:
    return ResolvedEntity(
        entity_type=record.entity_type,
        canonical_id=record.canonical_id,
        canonical_name=record.canonical_name,
        display_name=record.display_name,
        path=record.path,
        matched_surface=surface,
        matched_by=matched_by,
        confidence=confidence,
    )
