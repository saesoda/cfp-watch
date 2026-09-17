from __future__ import annotations

from pathlib import Path

import yaml

from .models import Journal


def load_journals(path: Path) -> list[Journal]:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return [
        Journal(
            key=item["key"],
            name=item["name"],
            aliases=list(item.get("aliases", [])),
            publisher=item.get("publisher", ""),
            direct_urls=list(item.get("direct_urls", [])),
            search_hints=list(item.get("search_hints", [])),
        )
        for item in raw
    ]
