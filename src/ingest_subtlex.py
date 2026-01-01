"""
ingest_subtlex (M1)
===================

Extract the top N Spanish lemmas from the Anki 5k deck (.apkg),
normalize them, and write data_intermediate/01_lemmas.tsv.

The Anki collection database lives inside the apkg zip as
collection.anki21 (preferred) or collection.anki2. Notes are read
from the "notes" table. Field ordering is defined by the model in the
"col" table, so we map field names to indices before extraction.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from common import (
    ensure_parent_dir,
    load_config,
    normalize_es,
    now_utc_iso,
    path_from_config,
    write_json,
)


FIELD_SPLIT = "\x1f"
RANK_FIELD_NAMES = {"rank", "ranking"}
SPANISH_FIELD_NAMES = {"spanish", "es", "lemma", "headword"}
POS_FIELD_NAMES = {"word type", "wordtype", "pos", "part of speech"}


@dataclass(frozen=True)
class FieldMap:
    rank_idx: int
    es_idx: int
    pos_idx: int | None


@dataclass(frozen=True)
class LemmaRow:
    rank: int
    es_display: str
    pos: str
    es_norm: str


def normalize_field_name(name: str) -> str:
    name = name.strip().lower()
    return re.sub(r"\s+", " ", name)


def find_field_index(
    field_names: list[str],
    candidates: set[str],
    *,
    allow_contains: bool = False,
) -> int | None:
    normalized_candidates = {normalize_field_name(name) for name in candidates}
    normalized = [normalize_field_name(name) for name in field_names]
    for idx, name in enumerate(normalized):
        if name in normalized_candidates:
            return idx
    if allow_contains:
        for idx, name in enumerate(normalized):
            if any(candidate in name for candidate in normalized_candidates):
                return idx
    return None


def build_field_map(field_names: list[str]) -> FieldMap:
    rank_idx = find_field_index(field_names, RANK_FIELD_NAMES, allow_contains=True)
    es_idx = find_field_index(field_names, SPANISH_FIELD_NAMES, allow_contains=False)
    if es_idx is None:
        es_idx = find_field_index(field_names, SPANISH_FIELD_NAMES, allow_contains=True)
    pos_idx = find_field_index(field_names, POS_FIELD_NAMES, allow_contains=False)
    if rank_idx is None or es_idx is None:
        raise ValueError(f"Required fields not found in model: {field_names}")
    return FieldMap(rank_idx=rank_idx, es_idx=es_idx, pos_idx=pos_idx)


def parse_rank(raw_value: str) -> int | None:
    if not raw_value:
        return None
    digits = re.sub(r"[^\d]", "", raw_value)
    if not digits:
        return None
    return int(digits)


@contextmanager
def open_apkg_db(apkg_path: Path) -> Iterable[sqlite3.Connection]:
    with zipfile.ZipFile(apkg_path, "r") as zf:
        if "collection.anki21" in zf.namelist():
            db_name = "collection.anki21"
        elif "collection.anki2" in zf.namelist():
            db_name = "collection.anki2"
        else:
            raise FileNotFoundError("No collection.anki21 or collection.anki2 found in apkg")
        db_bytes = zf.read(db_name)

    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(db_name).suffix) as tmp:
        tmp.write(db_bytes)
        tmp_path = Path(tmp.name)

    conn = sqlite3.connect(str(tmp_path))
    try:
        yield conn
    finally:
        conn.close()
        try:
            tmp_path.unlink()
        except OSError:
            pass


def load_models(conn: sqlite3.Connection) -> dict[int, list[str]]:
    cur = conn.execute("SELECT models FROM col LIMIT 1")
    row = cur.fetchone()
    if not row:
        raise ValueError("Missing models metadata in collection")
    models_raw = json.loads(row[0])
    models: dict[int, list[str]] = {}
    for mid, model in models_raw.items():
        fields = [field["name"] for field in model.get("flds", [])]
        models[int(mid)] = fields
    return models


def collect_candidates(
    conn: sqlite3.Connection, trailing_markers: list[str] | None
) -> list[LemmaRow]:
    models = load_models(conn)
    field_maps = {mid: build_field_map(fields) for mid, fields in models.items()}

    candidates: list[LemmaRow] = []
    for mid, flds in conn.execute("SELECT mid, flds FROM notes"):
        field_map = field_maps.get(mid)
        if field_map is None:
            continue
        fields = flds.split(FIELD_SPLIT)
        rank_raw = fields[field_map.rank_idx].strip() if field_map.rank_idx < len(fields) else ""
        rank = parse_rank(rank_raw)
        if rank is None or rank <= 0:
            continue
        es_display = fields[field_map.es_idx].strip() if field_map.es_idx < len(fields) else ""
        if not es_display:
            continue
        pos = ""
        if field_map.pos_idx is not None and field_map.pos_idx < len(fields):
            pos = fields[field_map.pos_idx].strip()
        es_norm = normalize_es(es_display, trailing_markers)
        if not es_norm:
            continue
        candidates.append(LemmaRow(rank=rank, es_display=es_display, pos=pos, es_norm=es_norm))
    return candidates


def select_top_n(candidates: list[LemmaRow], top_n: int) -> list[LemmaRow]:
    candidates.sort(key=lambda row: row.rank)
    seen: set[tuple[str, str]] = set()
    selected: list[LemmaRow] = []
    for row in candidates:
        key = (row.es_norm, row.pos)
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
        if len(selected) >= top_n:
            break
    return selected


def write_lemmas_tsv(path: Path, records: list[LemmaRow]) -> None:
    ensure_parent_dir(path)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["id", "es_display", "pos", "rank", "zipf", "es_norm"])
        for idx, row in enumerate(records, start=1):
            writer.writerow([idx, row.es_display, row.pos, row.rank, "", row.es_norm])


def update_build_info(
    path: Path,
    source_path: Path,
    note_count: int,
    candidates_count: int,
    lemma_count: int,
    top_n: int,
) -> None:
    payload = {
        "generated_on": now_utc_iso(),
        "lemma_count": lemma_count,
        "source": {
            "type": "anki_apkg",
            "path": str(source_path),
            "note_count": note_count,
            "candidate_count": candidates_count,
            "top_n": top_n,
        },
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
        if isinstance(existing, dict):
            existing.update(payload)
            payload = existing
    write_json(path, payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Anki 5k deck and produce 01_lemmas.tsv")
    parser.add_argument("--config", default="config.yml")
    args = parser.parse_args()

    config_path = Path(args.config)
    cfg = load_config(config_path)

    source_path = path_from_config(cfg, config_path, "sources", "subtlex_es", "path")
    lemmas_out = path_from_config(cfg, config_path, "outputs", "lemmas_tsv")
    build_info_out = path_from_config(cfg, config_path, "outputs", "build_info_json")

    top_n = int(cfg.get("sources", {}).get("subtlex_es", {}).get("top_n", 5000))
    trailing_markers = cfg.get("normalization", {}).get("strip_trailing_markers")
    if trailing_markers is not None and not isinstance(trailing_markers, list):
        raise SystemExit("normalization/strip_trailing_markers must be a list of strings")

    if not source_path.exists():
        raise SystemExit(f"Missing source apkg: {source_path}")

    with open_apkg_db(source_path) as conn:
        note_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        candidates = collect_candidates(conn, trailing_markers)

    records = select_top_n(candidates, top_n)
    write_lemmas_tsv(lemmas_out, records)
    update_build_info(
        build_info_out,
        source_path,
        note_count=note_count,
        candidates_count=len(candidates),
        lemma_count=len(records),
        top_n=top_n,
    )

    print(f"[m1] wrote {len(records)} lemmas to {lemmas_out}")


if __name__ == "__main__":
    main()
