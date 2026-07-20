from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class BoundedSafeLoader(yaml.SafeLoader):
    pass


def _no_duplicates_constructor(
    loader: yaml.SafeLoader,
    node: yaml.MappingNode,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        if key in mapping:
            raise ValueError(f"duplicate key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=False)
    return mapping


BoundedSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _no_duplicates_constructor,
)


def safe_load_bounded(
    path: Path,
    max_size: int = 1_000_000,
    max_nodes: int = 10_000,
) -> Any:
    if path.stat().st_size > max_size:
        raise ValueError(f"config file too large: {path}")
    loader = BoundedSafeLoader(path.read_text(encoding="utf-8"))
    try:
        node = loader.get_single_node()
        if node is None:
            return {}

        def count_nodes(item: yaml.Node | None) -> int:
            if item is None:
                return 0
            if isinstance(item, yaml.ScalarNode):
                return 1
            if isinstance(item, yaml.SequenceNode):
                return 1 + sum(count_nodes(child) for child in item.value)
            if isinstance(item, yaml.MappingNode):
                return 1 + sum(count_nodes(key) + count_nodes(value) for key, value in item.value)
            return 1

        if count_nodes(node) > max_nodes:
            raise ValueError("config file too complex")
        return loader.construct_document(node)
    finally:
        loader.dispose()
