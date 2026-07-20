from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from math import ceil
from pathlib import Path

import yaml


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    ascii_like = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(ascii_like.split())


@dataclass(frozen=True)
class SkillAssessment:
    verified: list[str]
    transferable: list[str]
    forbidden: list[str]
    violations: list[str]


class SkillPolicy:
    MENTION_ALIASES = {
        "qualite des donnees": [
            "data quality",
            "quality checks",
            "quality controls",
            "dataset reliability",
        ],
        "pipelines analytiques": [
            "data pipelines",
            "analytical pipelines",
            "analytics pipelines",
        ],
    }

    def __init__(
        self,
        verified: dict[str, list[str]],
        transferable: dict[str, list[str]],
        minimum_group_overlap: float,
        *,
        forbidden: dict[str, list[str]] | None = None,
        usage: dict[str, list[str]] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self._verified_display = verified
        self._transferable_display = transferable
        self._forbidden_display = forbidden or {}
        self._usage = usage or {}
        self._warnings = warnings or []
        self._minimum_group_overlap = minimum_group_overlap
        self._verified = {
            group: {_key(skill) for skill in skills} for group, skills in verified.items()
        }
        self._transferable = {
            group: {_key(skill) for skill in skills} for group, skills in transferable.items()
        }
        self._forbidden = {
            _key(skill) for skills in self._forbidden_display.values() for skill in skills
        }

    @classmethod
    def load(cls, path: Path) -> SkillPolicy:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        transferable: dict[str, list[str]] = {}
        for definition in (data.get("transferable") or {}).values():
            transferable.setdefault(definition["group"], []).extend(definition.get("skills", []))
        return cls(
            data.get("verified", {}),
            transferable,
            float(data.get("minimum_group_overlap", 0.75)),
            forbidden=data.get("forbidden", {}),
            usage=data.get("usage", {}),
            warnings=data.get("warnings", []),
        )

    def assess(
        self, groups: dict[str, list[str]], offer_text: str | None = None
    ) -> SkillAssessment:
        verified: list[str] = []
        transferable: list[str] = []
        forbidden: list[str] = []
        violations: list[str] = []
        normalized_offer = _key(offer_text) if offer_text is not None else None
        for group, skills in groups.items():
            canonical_group = self._canonical_group(group)
            verified_for_group = self._verified.get(canonical_group, set())
            transferable_for_group = self._transferable.get(canonical_group, set())
            retained = 0
            transferable_retained = 0
            for skill in skills:
                skill_key = _key(skill)
                if skill_key in self._forbidden:
                    forbidden.append(skill)
                elif skill_key in verified_for_group:
                    verified.append(skill)
                    retained += 1
                elif skill_key in transferable_for_group and self._offer_requests(
                    skill_key, normalized_offer
                ):
                    transferable.append(skill)
                    transferable_retained += 1
                else:
                    forbidden.append(skill)
            baseline_size = len(verified_for_group)
            minimum = ceil(baseline_size * self._minimum_group_overlap)
            coverage = retained + transferable_retained
            if coverage < minimum:
                message = (
                    f"{group}: {coverage}/{baseline_size} competences verifiees "
                    f"ou transferables conservees, minimum {minimum}"
                )
                violations.append(message)
        return SkillAssessment(
            list(dict.fromkeys(verified)),
            list(dict.fromkeys(transferable)),
            list(dict.fromkeys(forbidden)),
            violations,
        )

    @staticmethod
    def _offer_requests(skill_key: str, normalized_offer: str | None) -> bool:
        if normalized_offer is None:
            return True
        for match in SkillPolicy._exact_matches(skill_key, normalized_offer):
            if not SkillPolicy._is_negated_before(normalized_offer[: match.start()]):
                return True
        if SkillPolicy._nearby_phrase_match(skill_key, normalized_offer):
            return True
        return False

    @staticmethod
    def _exact_matches(skill_key: str, normalized_offer: str) -> list[re.Match[str]]:
        return list(re.finditer(rf"(?<!\w){re.escape(skill_key)}(?!\w)", normalized_offer))

    @staticmethod
    def _is_negated_before(context_before: str) -> bool:
        return bool(
            re.search(
                r"(?:aucun(?:e)?|sans|pas de|non requis(?:e)?|non demande(?:e)?|not required|no requirement for)\s+[^.!?]{0,45}$",
                context_before[-90:],
            )
        )

    @staticmethod
    def _nearby_phrase_match(skill_key: str, normalized_offer: str) -> bool:
        tokens = [token for token in re.findall(r"\w+", skill_key) if len(token) > 2]
        if len(tokens) < 2:
            return False
        first_token = tokens[0]
        for match in re.finditer(rf"(?<!\w){re.escape(first_token)}(?!\w)", normalized_offer):
            if SkillPolicy._is_negated_before(normalized_offer[: match.start()]):
                continue
            window = normalized_offer[match.start() : match.start() + 35]
            if all(re.search(rf"(?<!\w){re.escape(token)}(?!\w)", window) for token in tokens[1:]):
                return True
        return False

    def _canonical_group(self, group: str) -> str:
        if group in self._verified:
            return group
        group_key = _key(group)
        for existing in self._verified:
            existing_key = _key(existing)
            if group_key == existing_key:
                return existing
        return group

    @property
    def verified_groups(self) -> set[str]:
        return set(self._verified_display.keys())

    def context_data(self) -> dict[str, object]:
        """Return the candidate-bound policy fields needed in a context capsule."""
        return {
            "minimum_group_overlap": self._minimum_group_overlap,
            "transferable": {
                group: list(skills) for group, skills in sorted(self._transferable_display.items())
            },
            "verified": {
                group: list(skills) for group, skills in sorted(self._verified_display.items())
            },
            "forbidden": {
                group: list(skills) for group, skills in sorted(self._forbidden_display.items())
            },
            "usage": {level: list(skills) for level, skills in sorted(self._usage.items())},
            "warnings": list(self._warnings),
        }

    def is_verified(self, skill: str, group: str) -> bool:
        skill_key = _key(skill)
        return skill_key not in self._forbidden and skill_key in self._verified.get(group, set())

    def is_allowed(self, skill: str, group: str, offer_text: str | None = None) -> bool:
        if _key(skill) in self._forbidden:
            return False
        canonical_group = self._canonical_group(group)
        if self.is_verified(skill, canonical_group):
            return True
        normalized_offer = _key(offer_text) if offer_text is not None else None
        return _key(skill) in self._transferable.get(
            canonical_group, set()
        ) and self._offer_requests(_key(skill), normalized_offer)

    def is_requested_transferable(
        self, skill: str, group: str, offer_text: str | None = None
    ) -> bool:
        canonical_group = self._canonical_group(group)
        normalized_offer = _key(offer_text) if offer_text is not None else None
        skill_key = _key(skill)
        if skill_key in self._forbidden:
            return False
        return skill_key in self._transferable.get(canonical_group, set()) and self._offer_requests(
            skill_key, normalized_offer
        )

    def offer_mentions(self, skill: str, offer_text: str | None = None) -> int:
        if offer_text is None:
            return 0
        normalized_offer = _key(offer_text)
        skill_key = _key(skill)
        mention_keys = [skill_key, *self.MENTION_ALIASES.get(skill_key, [])]
        return sum(
            len(list(re.finditer(rf"(?<!\w){re.escape(mention_key)}(?!\w)", normalized_offer)))
            for mention_key in mention_keys
        )

    def canonical_group(self, group: str) -> str:
        return self._canonical_group(group)

    def verified_skills(self, group: str) -> list[str]:
        canonical_group = self._canonical_group(group)
        return list(self._verified_display.get(canonical_group, []))

    def requested_transferables(self, offer_text: str) -> dict[str, list[str]]:
        normalized_offer = _key(offer_text)
        requested: dict[str, list[str]] = {}
        for group, skills in self._transferable_display.items():
            for skill in skills:
                if self._offer_requests(_key(skill), normalized_offer):
                    requested.setdefault(group, []).append(skill)
        return requested

    def prompt_text(self) -> str:
        verified = "\n".join(
            f"- {group}: {', '.join(skills)}" for group, skills in self._verified_display.items()
        )
        transferable = "\n".join(
            f"- {group}: {', '.join(skills)}"
            for group, skills in self._transferable_display.items()
        )
        return (
            f"Compétences vérifiées par catégorie:\n{verified}\n"
            f"Compétences proches autorisées mais à confirmer par catégorie:\n{transferable}\n"
            "Toute autre compétence est interdite. Ne sélectionne une compétence à confirmer que "
            "si l'offre la demande réellement et qu'elle remplace ou complète une compétence proche. "
            f"Conserve au moins {self._minimum_group_overlap:.0%} de couverture par catégorie, "
            "en comptant les compétences vérifiées et les compétences transférables explicitement demandées."
        )

    def agentic_prompt_text(self) -> str:
        """Evidence catalogue for lean writers; selection is driven by the current offer."""
        verified = "\n".join(
            f"- {group}: {', '.join(skills)}" for group, skills in self._verified_display.items()
        )
        transferable = "\n".join(
            f"- {group}: {', '.join(skills)}"
            for group, skills in self._transferable_display.items()
        )
        return (
            "Catalogue de preuves techniques verifiees (les categories historiques servent uniquement "
            f"a retrouver les preuves):\n{verified}\n"
            "Catalogue de preuves transferables ou preparables lorsqu'elles sont coherentes avec le profil "
            f"et demandees par l'offre actuelle:\n{transferable}\n"
            "Choisis les competences depuis l'offre actuelle et le brief source. Tu peux renommer, fusionner "
            "ou omettre les categories historiques. N'ajoute que des capacites, methodes, outils, plateformes "
            "ou frameworks techniques defendables. Conserve un warning interne pour tout signal transferable "
            "ou prepare, sans afficher de disclaimer dans le CV."
        )


def manual_review_warnings(assessment: SkillAssessment) -> list[str]:
    warnings = [
        f"Compétence transférable à vérifier avant envoi: {skill}"
        for skill in assessment.transferable
    ]
    warnings.extend(
        f"Politique de compétences à vérifier: {issue}" for issue in assessment.violations
    )
    return warnings
