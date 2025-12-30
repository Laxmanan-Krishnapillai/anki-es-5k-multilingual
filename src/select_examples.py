from __future__ import annotations

import argparse
from pathlib import Path

from common import load_config, path_from_config, write_tsv_header


def select_examples(cfg: dict, config_path: Path) -> None:
    examples_out = path_from_config(cfg, config_path, "outputs", "examples_tsv")
    write_tsv_header(examples_out, ["es_norm", "example", "source"])
    print(f"[stub] wrote {examples_out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Select curated example sentences")
    parser.add_argument("--config", default="config.yml")
    args = parser.parse_args()
    config_path = Path(args.config)
    cfg = load_config(config_path)
    select_examples(cfg, config_path)


if __name__ == "__main__":
    main()
