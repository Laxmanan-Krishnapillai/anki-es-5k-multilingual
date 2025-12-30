from __future__ import annotations

import argparse
from pathlib import Path

from common import load_config, path_from_config, write_csv_header


def export_csv(cfg: dict, config_path: Path) -> None:
    out_csv = path_from_config(cfg, config_path, "outputs", "deck_csv")
    headers = [
        "ID",
        "Spanish",
        "IPA",
        "Example",
        "Tamil",
        "German",
        "French",
        "POS",
        "FrequencyRank",
        "Zipf",
        "Sources",
        "Flags",
        "Notes",
    ]
    write_csv_header(out_csv, headers)
    print(f"[stub] wrote {out_csv}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Anki CSV")
    parser.add_argument("--config", default="config.yml")
    args = parser.parse_args()
    config_path = Path(args.config)
    cfg = load_config(config_path)
    export_csv(cfg, config_path)


if __name__ == "__main__":
    main()
