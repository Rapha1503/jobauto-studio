from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jobauto.submission_preferences import SubmissionMode, SubmissionPreferences


def test_submission_preferences_load_candidate_owned_policy(tmp_path: Path) -> None:
    path = tmp_path / "submission.yaml"
    path.write_text(
        """schema_version: 1
mode: automatic
max_applications_per_campaign: 8
allowed_portals: [Greenhouse, greenhouse, Workday]
standard_answers:
  work authorization: France
allowed_consents: [privacy policy]
max_retries: 2
on_login: pause
on_captcha: request_user
on_two_factor: request_user
on_ambiguous_field: pause
require_confirmation_evidence: true
""",
        encoding="utf-8",
    )

    preferences = SubmissionPreferences.load(path)

    assert preferences.mode is SubmissionMode.AUTOMATIC
    assert preferences.allowed_portals == ["Greenhouse", "Workday"]
    assert preferences.standard_answers == {"work authorization": "France"}
    assert preferences.max_retries == 2


def test_submission_preferences_reject_unknown_or_unbounded_values() -> None:
    with pytest.raises(ValidationError):
        SubmissionPreferences.model_validate({"mode": "always_submit"})
    with pytest.raises(ValidationError):
        SubmissionPreferences.model_validate({"max_applications_per_campaign": 1000})
    with pytest.raises(ValidationError):
        SubmissionPreferences.model_validate({"on_captcha": "bypass"})
