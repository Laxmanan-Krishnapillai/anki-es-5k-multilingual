from __future__ import annotations

import argparse
from pathlib import Path

from common import load_config, path_from_config, write_tsv_header


def parse_wiktionary(cfg: dict, config_path: Path) -> None:
    dump_path = path_from_config(cfg, config_path, "sources", "wiktionary_dump", "path")
    ipa_out = path_from_config(cfg, config_path, "outputs", "ipa_tsv")
    meta_out = path_from_config(cfg, config_path, "outputs", "wikt_meta_tsv")
    wikt_out = path_from_config(cfg, config_path, "outputs", "wikt_fr_de_tsv")
    es_ta_out = path_from_config(cfg, config_path, "outputs", "es_ta_tsv")
    if not dump_path.exists():
        print(f"[stub] source not found: {dump_path}")
    write_tsv_header(ipa_out, ["es_norm", "ipa"])
    write_tsv_header(meta_out, ["es_norm", "page_id", "revision_id", "revision_timestamp"])
    write_tsv_header(wikt_out, ["es_norm", "lang", "target", "gloss"])
    write_tsv_header(es_ta_out, ["es_norm", "ta", "source", "gloss"])
    print(f"[stub] wrote {ipa_out}")
    print(f"[stub] wrote {meta_out}")
    print(f"[stub] wrote {wikt_out}")
    print(f"[stub] wrote {es_ta_out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse Wiktionary IPA and translations")
    parser.add_argument("--config", default="config.yml")
    args = parser.parse_args()
    config_path = Path(args.config)
    cfg = load_config(config_path)
    parse_wiktionary(cfg, config_path)


if __name__ == "__main__":
    main()
