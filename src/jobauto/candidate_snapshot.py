from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType

from jobauto.adaptation_policy import AdaptationPolicy
from jobauto.candidate_profile import CandidateProfile
from jobauto.cv_source import CvSourceDocument
from jobauto.facts import FactStore
from jobauto.latex_cv_source import LatexCvMapping, validate_mapping_source
from jobauto.project_bank import ProjectBank
from jobauto.search_preferences import SearchPreferences
from jobauto.skills import SkillPolicy
from jobauto.submission_preferences import SubmissionPreferences

_EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(?:phone|tel(?:ephone)?)[\s:]*([^|]+)", re.IGNORECASE)
_CLAIM_VALUE_PATTERN = re.compile(
    r"(?<!\w)(?P<prefix>EUR|USD|GBP|[€$£])?\s*"
    r"(?P<number>\d+(?:[\s\u00a0.,]\d+)*)\s*"
    r"(?P<scale>thousands?|millions?|milliards?|billions?|[kKmMbB])?\s*"
    r"(?P<suffix>%|EUR|USD|GBP|[€$£])?(?!\w)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CandidateSnapshot:
    _profile: CandidateProfile = field(repr=False)
    _cv_source: CvSourceDocument = field(repr=False)
    _facts: FactStore = field(repr=False)
    _project_bank: ProjectBank = field(repr=False)
    _skill_policy: SkillPolicy = field(repr=False)
    _adaptation_policy: AdaptationPolicy = field(repr=False)
    _search_preferences: SearchPreferences = field(repr=False)
    _submission_preferences: SubmissionPreferences = field(repr=False)
    _cv_template: str = field(repr=False)
    _cv_template_bytes: bytes = field(repr=False)
    _letter_reference: str = field(repr=False)
    _cv_mapping: LatexCvMapping | None = field(repr=False)
    asset_hashes: Mapping[str, str]
    snapshot_hash: str

    @property
    def profile(self) -> CandidateProfile:
        return deepcopy(self._profile)

    @property
    def cv_source(self) -> CvSourceDocument:
        return deepcopy(self._cv_source)

    @property
    def facts(self) -> FactStore:
        return deepcopy(self._facts)

    @property
    def project_bank(self) -> ProjectBank:
        return deepcopy(self._project_bank)

    @property
    def skill_policy(self) -> SkillPolicy:
        return deepcopy(self._skill_policy)

    @property
    def adaptation_policy(self) -> AdaptationPolicy:
        return deepcopy(self._adaptation_policy)

    @property
    def search_preferences(self) -> SearchPreferences:
        return deepcopy(self._search_preferences)

    @property
    def submission_preferences(self) -> SubmissionPreferences:
        return deepcopy(self._submission_preferences)

    @property
    def cv_template(self) -> str:
        return self._cv_template

    @property
    def cv_template_bytes(self) -> bytes:
        return self._cv_template_bytes

    @property
    def letter_reference(self) -> str:
        return self._letter_reference

    @property
    def cv_mapping(self) -> LatexCvMapping | None:
        return deepcopy(self._cv_mapping)

    @property
    def evidence_ids(self) -> frozenset[str]:
        return frozenset(
            [
                *(fact.fact_id for fact in self._facts.facts if fact.status.value == "verified"),
                *(
                    f"project.{entry.id}"
                    for entry in self._project_bank.entries
                    if entry.cv_eligible or entry.context_eligible
                ),
                *(
                    f"source_block.{block.block_id}"
                    for block in (self._cv_mapping.blocks if self._cv_mapping is not None else [])
                    if block.kind.value != "identity"
                ),
            ]
        )

    def require_evidence_ids(self, evidence_ids: list[str]) -> None:
        fact_ids = {fact.fact_id for fact in self._facts.facts if fact.status.value == "verified"}
        project_ids = {
            f"project.{entry.id}"
            for entry in self._project_bank.entries
            if entry.cv_eligible or entry.context_eligible
        }
        source_block_ids = {
            f"source_block.{block.block_id}"
            for block in (self._cv_mapping.blocks if self._cv_mapping is not None else [])
            if block.kind.value != "identity"
        }
        for evidence_id in evidence_ids:
            if evidence_id in fact_ids:
                self._facts.require(evidence_id)
            elif evidence_id not in project_ids and evidence_id not in source_block_ids:
                raise KeyError(f"Unknown candidate fact or evidence: {evidence_id}")

    def require_protected_claim_values(self, text: str, evidence_ids: list[str]) -> None:
        protected_ids = set(self._profile.protected_claims) & set(evidence_ids)
        if not protected_ids:
            return
        rendered_values = _claim_value_markers(text)
        for fact_id in sorted(protected_ids):
            fact = self._facts.require(fact_id)
            expected_values = _claim_value_markers(fact.claim)
            if expected_values and not expected_values.issubset(rendered_values):
                missing = ", ".join(sorted(expected_values - rendered_values))
                raise ValueError(
                    f"Protected claim values changed or were omitted for {fact_id}: {missing}"
                )

    def require_supported_claim_values(
        self,
        text: str,
        evidence_ids: list[str],
        *,
        additional_evidence: list[str] | None = None,
    ) -> None:
        """Reject quantitative claims that are absent from the cited candidate evidence."""
        rendered_values = _claim_value_markers(text)
        if not rendered_values:
            return
        allowed_values: set[str] = set()
        for evidence_id in dict.fromkeys(evidence_ids):
            allowed_values.update(_claim_value_markers(self._evidence_text(evidence_id)))
        for evidence_text in additional_evidence or []:
            allowed_values.update(_claim_value_markers(evidence_text))
        unsupported = rendered_values - allowed_values
        if unsupported:
            details = ", ".join(sorted(unsupported))
            raise ValueError(f"Unsupported quantitative claim values: {details}")

    def _evidence_text(self, evidence_id: str) -> str:
        fact_ids = {fact.fact_id for fact in self._facts.facts if fact.status.value == "verified"}
        if evidence_id in fact_ids:
            return self._facts.require(evidence_id).claim
        if evidence_id.startswith("project."):
            project = self._project_bank.get(evidence_id.removeprefix("project."))
            return "\n".join(
                [
                    project.title,
                    project.default_stack_line,
                    *project.cv_bullets,
                    *project.letter_angles,
                ]
            )
        if evidence_id.startswith("source_block.") and self._cv_mapping is not None:
            block_id = evidence_id.removeprefix("source_block.")
            block = next(
                (item for item in self._cv_mapping.blocks if item.block_id == block_id),
                None,
            )
            if block is not None and block.kind.value != "identity":
                return self._cv_template_bytes[block.start_byte : block.end_byte].decode(
                    self._cv_mapping.encoding
                )
        raise KeyError(f"Unknown candidate fact or evidence: {evidence_id}")


class CandidateProfileRepository:
    def __init__(self, profiles_root: Path) -> None:
        self._profiles_root = profiles_root.expanduser().resolve()
        if not self._profiles_root.is_dir():
            raise ValueError(f"Candidate profiles root does not exist: {self._profiles_root}")

    def load_snapshot(self, profile_path: Path) -> CandidateSnapshot:
        source_path = profile_path.expanduser().resolve()
        try:
            source_path.relative_to(self._profiles_root)
        except ValueError as exc:
            raise ValueError("candidate profile must be located under the profiles root") from exc

        profile = CandidateProfile.load(source_path)
        _validate_asset_paths(profile, self._profiles_root)
        cv_source_path = _required_asset(profile.cv_source_path, "cv_source_path")
        adaptation_policy_path = _required_asset(
            profile.adaptation_policy_path, "adaptation_policy_path"
        )
        cv_source = CvSourceDocument.parse(cv_source_path.read_text(encoding="utf-8"))
        facts = FactStore.load(profile.facts_path)
        project_bank = ProjectBank.load(profile.project_bank_path)
        skill_policy = SkillPolicy.load(profile.skill_policy_path)
        adaptation_policy = AdaptationPolicy.load(adaptation_policy_path)
        search_preferences = (
            SearchPreferences.load(profile.search_preferences_path)
            if profile.search_preferences_path is not None
            else SearchPreferences()
        )
        submission_preferences = (
            SubmissionPreferences.load(profile.submission_preferences_path)
            if profile.submission_preferences_path is not None
            else SubmissionPreferences()
        )
        cv_template_bytes = profile.cv_model_path.read_bytes()
        cv_template = cv_template_bytes.decode("utf-8-sig")
        if not cv_template.strip():
            raise ValueError("candidate CV model is empty")
        letter_reference = profile.letter_model_path.read_text(encoding="utf-8").strip()
        cv_mapping = (
            LatexCvMapping.load(profile.cv_mapping_path)
            if profile.cv_mapping_path is not None
            else None
        )
        if cv_mapping is not None:
            source_sha256 = hashlib.sha256(cv_template_bytes).hexdigest()
            if cv_mapping.source_sha256 != source_sha256:
                raise ValueError("candidate CV mapping does not match exact LaTeX source")
            validate_mapping_source(cv_template_bytes, cv_mapping)

        _validate_identity(profile, cv_source)
        _validate_evidence(profile, cv_source, facts, project_bank, skill_policy, adaptation_policy)
        asset_hashes = _asset_hashes(profile, source_path)
        snapshot_hash = _snapshot_hash(asset_hashes)
        return CandidateSnapshot(
            _profile=profile,
            _cv_source=cv_source,
            _facts=facts,
            _project_bank=project_bank,
            _skill_policy=skill_policy,
            _adaptation_policy=adaptation_policy,
            _search_preferences=search_preferences,
            _submission_preferences=submission_preferences,
            _cv_template=cv_template,
            _cv_template_bytes=cv_template_bytes,
            _letter_reference=letter_reference,
            _cv_mapping=cv_mapping,
            asset_hashes=MappingProxyType(asset_hashes),
            snapshot_hash=snapshot_hash,
        )


def _required_asset(path: Path | None, field_name: str) -> Path:
    if path is None:
        raise ValueError(f"candidate snapshot requires {field_name}")
    return path


def _validate_asset_paths(profile: CandidateProfile, profiles_root: Path) -> None:
    for name, path in profile.asset_paths.items():
        try:
            path.relative_to(profiles_root)
        except ValueError as exc:
            raise ValueError(
                f"candidate asset {name} must be located under the profiles root"
            ) from exc


def _validate_identity(profile: CandidateProfile, cv_source: CvSourceDocument) -> None:
    identity = profile.identity
    expected_name = _normalize_text(f"{identity.first_name} {identity.last_name}")
    cv_email = _extract_email(cv_source.contact_line)
    cv_phone = _extract_phone(cv_source.contact_line)
    if (
        _normalize_text(cv_source.name) != expected_name
        or cv_email != _normalize_email(identity.email)
        or (identity.phone is not None and cv_phone != _normalize_phone(identity.phone))
    ):
        raise ValueError("candidate identity does not match CV source")


def _validate_evidence(
    profile: CandidateProfile,
    cv_source: CvSourceDocument,
    facts: FactStore,
    project_bank: ProjectBank,
    skill_policy: SkillPolicy,
    adaptation_policy: AdaptationPolicy,
) -> None:
    for fact_id in profile.protected_claims:
        facts.require(fact_id)
    for document in adaptation_policy.documents.values():
        for section in document.sections.values():
            for fact_id in section.protected_fact_ids:
                facts.require(fact_id)

    project_titles = {_normalize_text(entry.title) for entry in project_bank.entries}
    missing_projects = sorted(
        entry.title
        for entry in cv_source.projects
        if _normalize_text(entry.title) not in project_titles
    )
    if missing_projects:
        raise ValueError(f"CV source projects are missing from project bank: {missing_projects}")

    if cv_source.skills and not skill_policy.verified_groups:
        raise ValueError("CV source skills require verified skill groups")
    missing_skills = sorted(
        f"{group}: {skill}"
        for group, skills in cv_source.skills.items()
        for skill in skills
        if not skill_policy.is_allowed(skill, skill_policy.canonical_group(group))
    )
    if missing_skills:
        raise ValueError(f"CV source skills are not verified by skill policy: {missing_skills}")


def _asset_hashes(profile: CandidateProfile, source_path: Path) -> dict[str, str]:
    asset_paths = {"profile": source_path, **profile.asset_paths}
    return {name: _file_sha256(path) for name, path in sorted(asset_paths.items())}


def _snapshot_hash(asset_hashes: Mapping[str, str]) -> str:
    payload = json.dumps(asset_hashes, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _extract_email(contact_line: str) -> str:
    match = _EMAIL_PATTERN.search(contact_line)
    if match is None:
        return ""
    return _normalize_email(match.group())


def _extract_phone(contact_line: str) -> str:
    match = _PHONE_PATTERN.search(contact_line)
    if match is None:
        return ""
    return _normalize_phone(match.group(1))


def _normalize_email(value: str) -> str:
    return value.strip().casefold()


def _normalize_phone(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(
            character for character in normalized if not unicodedata.combining(character)
        ).split()
    )


def _claim_value_markers(value: str) -> set[str]:
    markers: set[str] = set()
    for match in _CLAIM_VALUE_PATTERN.finditer(value):
        number = _canonical_number(match.group("number"))
        if number is None:
            continue
        prefix = _canonical_unit(match.group("prefix"))
        suffix = _canonical_unit(match.group("suffix"))
        scale = _canonical_scale(match.group("scale"))
        if not (prefix or suffix or scale) and _looks_like_calendar_year(value, match, number):
            continue
        markers.add(f"{_apply_scale(number, scale)}|{prefix or suffix}")
    return markers


def _looks_like_calendar_year(value: str, match: re.Match[str], number: str) -> bool:
    if not number.isdigit() or not 1900 <= int(number) <= 2100:
        return False
    context = value[max(0, match.start() - 24) : min(len(value), match.end() + 8)]
    normalized = _normalize_text(context)
    date_words = {
        "depuis",
        "since",
        "from",
        "jusqu",
        "septembre",
        "octobre",
        "novembre",
        "decembre",
        "janvier",
        "fevrier",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "aout",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    }
    if any(word in normalized.split() for word in date_words):
        return True
    return bool(re.search(r"\b(?:19|20)\d{2}\s*[-/–—]\s*(?:19|20)\d{2}\b", context))


def _canonical_number(value: str) -> str | None:
    compact = value.replace(" ", "").replace("\u00a0", "")
    if not compact:
        return None
    decimal_separator: str | None = None
    if "." in compact and "," in compact:
        decimal_separator = "." if compact.rfind(".") > compact.rfind(",") else ","
    elif "." in compact or "," in compact:
        separator = "." if "." in compact else ","
        tail = compact.rsplit(separator, 1)[1]
        head = compact.split(separator, 1)[0]
        decimal_separator = None if len(tail) == 3 and head != "0" else separator
    if decimal_separator is None:
        normalized = compact.replace(".", "").replace(",", "")
    else:
        thousands_separator = "," if decimal_separator == "." else "."
        normalized = compact.replace(thousands_separator, "").replace(decimal_separator, ".")
    try:
        decimal = Decimal(normalized)
    except InvalidOperation:
        return None
    rendered = format(decimal.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _canonical_unit(value: str | None) -> str:
    unit = (value or "").casefold()
    return {
        "€": "eur",
        "eur": "eur",
        "$": "usd",
        "usd": "usd",
        "£": "gbp",
        "gbp": "gbp",
        "%": "%",
    }.get(unit, unit)


def _canonical_scale(value: str | None) -> str:
    scale = (value or "").casefold()
    return {
        "thousand": "k",
        "thousands": "k",
        "million": "m",
        "millions": "m",
        "milliard": "b",
        "milliards": "b",
        "billion": "b",
        "billions": "b",
    }.get(scale, scale)


def _apply_scale(number: str, scale: str) -> str:
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(scale, 1)
    value = Decimal(number) * multiplier
    rendered = format(value.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
