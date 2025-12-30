from __future__ import annotations

import argparse
from pathlib import Path

from common import load_config, path_from_config, write_tsv_header


def ingest_subtlex(cfg: dict, config_path: Path) -> None:
    src = path_from_config(cfg, config_path, "sources", "subtlex_es", "path")
    out = path_from_config(cfg, config_path, "outputs", "lemmas_tsv")
    if not src.exists():
        print(f"[stub] source not found: {src}")
    headers = ["id", "es_display", "es_norm", "pos", "rank", "zipf"]
    write_tsv_header(out, headers)
    print(f"[stub] wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest SUBTLEX-ES lemmas")
    parser.add_argument("--config", default="config.yml")
    args = parser.parse_args()
    config_path = Path(args.config)
    cfg = load_config(config_path)
    ingest_subtlex(cfg, config_path)


if __name__ == "__main__":
    main()
