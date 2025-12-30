from __future__ import annotations

import argparse
from pathlib import Path

from common import load_config, path_from_config, write_tsv_header


def parse_dictionaries(cfg: dict, config_path: Path) -> None:
    outputs = {
        "es_fr_tsv": ["es_norm", "fr", "source", "gloss"],
        "es_de_tsv": ["es_norm", "de", "source", "gloss"],
        "es_en_tsv": ["es_norm", "en", "source", "gloss"],
        "en_ta_tsv": ["en_norm", "ta", "source", "gloss"],
    }

    for key, headers in outputs.items():
        out = path_from_config(cfg, config_path, "outputs", key)
        write_tsv_header(out, headers)
        print(f"[stub] wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse bilingual dictionaries")
    parser.add_argument("--config", default="config.yml")
    args = parser.parse_args()
    config_path = Path(args.config)
    cfg = load_config(config_path)
    parse_dictionaries(cfg, config_path)


if __name__ == "__main__":
    main()
