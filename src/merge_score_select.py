from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from common import ensure_parent_dir, load_config, path_from_config, write_tsv_header


SCHEMA = """
CREATE TABLE IF NOT EXISTS lemma_records (
    id INTEGER PRIMARY KEY,
    es TEXT NOT NULL,
    es_norm TEXT NOT NULL,
    pos TEXT,
    rank INTEGER,
    zipf REAL,
    ipa TEXT,
    example_es TEXT,
    example_source TEXT,
    fr_primary TEXT,
    de_primary TEXT,
    ta_primary TEXT,
    fr_alt TEXT,
    de_alt TEXT,
    ta_alt TEXT,
    sources TEXT,
    flags TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_lemma_records_es_norm ON lemma_records(es_norm);
"""


def init_db(db_path: Path) -> None:
    ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def merge_score_select(cfg: dict, config_path: Path) -> None:
    db_path = path_from_config(cfg, config_path, "paths", "deck_db")
    init_db(db_path)
    print(f"[stub] initialized {db_path}")
    pivot_out = path_from_config(cfg, config_path, "outputs", "es_ta_pivoted_tsv")
    write_tsv_header(pivot_out, ["es_norm", "ta", "source", "pivot_en", "gloss"])
    print(f"[stub] wrote {pivot_out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge, score, and select candidates")
    parser.add_argument("--config", default="config.yml")
    args = parser.parse_args()
    config_path = Path(args.config)
    cfg = load_config(config_path)
    merge_score_select(cfg, config_path)


if __name__ == "__main__":
    main()
