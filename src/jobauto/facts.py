from __future__ import annotations

from pathlib import Path

import yaml

from jobauto.models import CandidateFact


class FactStore:
    def __init__(self, facts: list[CandidateFact]) -> None:
        if not facts:
            raise ValueError("fact store has no facts")
        fact_ids = [fact.fact_id for fact in facts]
        duplicates = sorted({fact_id for fact_id in fact_ids if fact_ids.count(fact_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate fact ids: {duplicates}")
        self._facts = {fact.fact_id: fact for fact in facts}

    @classmethod
    def load(cls, path: Path) -> FactStore:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls([CandidateFact.model_validate(item) for item in data.get("facts", [])])

    def require(self, fact_id: str) -> CandidateFact:
        try:
            return self._facts[fact_id].require_approved()
        except KeyError as exc:
            raise KeyError(f"Unknown candidate fact: {fact_id}") from exc

    def require_all(self, fact_ids: list[str]) -> list[CandidateFact]:
        return [self.require(fact_id) for fact_id in fact_ids]

    @property
    def facts(self) -> tuple[CandidateFact, ...]:
        return tuple(self._facts.values())

    def prompt_text(self) -> str:
        return "\n".join(
            f"- {fact.fact_id}: {fact.claim}"
            for fact in self._facts.values()
            if fact.status.value == "verified"
        )
