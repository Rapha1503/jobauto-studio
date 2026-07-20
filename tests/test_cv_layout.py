from jobauto.adaptation_policy import CvLayoutPolicy
from jobauto.cv_layout import apply_cv_layout, apply_cv_section_spacing, cv_layout_choices


def test_layout_choices_start_readable_and_end_at_configured_minimum() -> None:
    policy = CvLayoutPolicy(
        minimum_font_size_pt=9.5,
        maximum_font_size_pt=11.0,
        minimum_line_height_ratio=1.10,
        maximum_line_height_ratio=1.18,
    )

    choices = cv_layout_choices(policy)

    assert choices[0].font_size_pt == 11.0
    assert choices[0].line_height_ratio == 1.18
    assert choices[-1].font_size_pt == 9.5
    assert choices[-1].line_height_ratio == 1.10
    assert len(choices) == 12
    assert choices.index(
        next(
            item for item in choices if (item.font_size_pt, item.line_height_ratio) == (11.0, 1.14)
        )
    ) < choices.index(
        next(
            item for item in choices if (item.font_size_pt, item.line_height_ratio) == (10.5, 1.14)
        )
    )


def test_layout_choices_prefer_balanced_readability_over_cramped_leading() -> None:
    choices = cv_layout_choices(CvLayoutPolicy())

    balanced = choices.index(
        next(item for item in choices if (item.font_size_pt, item.line_height_ratio) == (11.5, 1.3))
    )
    cramped = choices.index(
        next(item for item in choices if (item.font_size_pt, item.line_height_ratio) == (10.5, 1.1))
    )

    assert balanced < cramped


def test_layout_choices_prefer_larger_type_when_leading_remains_comfortable() -> None:
    choices = cv_layout_choices(CvLayoutPolicy())

    larger = choices.index(
        next(item for item in choices if (item.font_size_pt, item.line_height_ratio) == (12.0, 1.3))
    )
    smaller = choices.index(
        next(item for item in choices if (item.font_size_pt, item.line_height_ratio) == (10.5, 1.4))
    )

    assert larger < smaller


def test_default_layout_checks_intermediate_spacing_levels() -> None:
    choices = cv_layout_choices(CvLayoutPolicy())

    assert any(item.font_size_pt == 11.0 and item.line_height_ratio == 1.2 for item in choices)


def test_layout_override_preserves_preamble_and_line_endings() -> None:
    source = b"\\documentclass{article}\r\n\\begin{document}\r\nHello\r\n\\end{document}\r\n"
    choice = cv_layout_choices(CvLayoutPolicy())[0]

    rendered = apply_cv_layout(source, choice)

    assert isinstance(rendered, bytes)
    assert rendered.startswith(b"\\documentclass{article}\r\n\\begin{document}\r\n")
    assert b"% JOBAUTO_LAYOUT\r\n\\fontsize{12}{18}\\selectfont" in rendered
    assert rendered.endswith(b"Hello\r\n\\end{document}\r\n")


def test_section_spacing_preserves_first_section_and_spaces_following_sections() -> None:
    source = (
        "\\begin{document}\n"
        "\\cvsection{Profile}\nText\n"
        "\\cvsection{Experience}\nText\n"
        "\\section*{Education}\nText\n"
        "\\end{document}\n"
    )

    rendered = apply_cv_section_spacing(source, 8)

    assert rendered.count("% JOBAUTO_SECTION_SPACING") == 2
    assert "\\cvsection{Profile}\nText\n% JOBAUTO_SECTION_SPACING" in rendered
    assert "\\vspace{8pt}\n\\cvsection{Experience}" in rendered
    assert "\\vspace{8pt}\n\\section*{Education}" in rendered


def test_section_spacing_supports_source_defined_section_macros() -> None:
    source = (
        "\\begin{document}\n"
        "\\resumeSection{Profile}\nText\n"
        "\\sectionTitle{Publications}\nText\n"
        "\\end{document}\n"
    )

    rendered = apply_cv_section_spacing(source, 6)

    assert rendered.count("% JOBAUTO_SECTION_SPACING") == 1
    assert "\\vspace{6pt}\n\\sectionTitle{Publications}" in rendered


def test_section_spacing_uses_confirmed_arbitrary_section_command() -> None:
    source = (
        "\\begin{document}\n"
        "\\rubrique{Profile}\nText\n"
        "\\rubrique{Publications}\nText\n"
        "\\end{document}\n"
    )

    rendered = apply_cv_section_spacing(source, 6, section_commands={"rubrique"})

    assert rendered.count("% JOBAUTO_SECTION_SPACING") == 1
    assert "\\vspace{6pt}\n\\rubrique{Publications}" in rendered


def test_section_spacing_never_treats_preamble_configuration_as_cv_sections() -> None:
    source = (
        "\\documentclass{article}\n"
        "\\sectionfont{\\large}\n"
        "\\sectioncolor{blue}\n"
        "\\begin{document}\n"
        "\\section{Profile}\nText\n"
        "\\section{Experience}\nText\n"
        "\\end{document}\n"
    )

    rendered = apply_cv_section_spacing(source, 6)

    preamble, body = rendered.split("\\begin{document}", maxsplit=1)
    assert "% JOBAUTO_SECTION_SPACING" not in preamble
    assert body.count("% JOBAUTO_SECTION_SPACING") == 1
    assert "\\vspace{6pt}\n\\section{Experience}" in body


def test_section_spacing_ignores_non_structural_sectionmark_in_document_body() -> None:
    source = (
        "\\begin{document}\n"
        "\\sectionmark{Resume}\n"
        "\\section{Profile}\nText\n"
        "\\section{Experience}\nText\n"
        "\\end{document}\n"
    )

    rendered = apply_cv_section_spacing(source, 6)

    assert rendered.count("% JOBAUTO_SECTION_SPACING") == 1
    assert "% JOBAUTO_SECTION_SPACING\n\\vspace{6pt}\n\\section{Profile}" not in rendered
    assert "\\vspace{6pt}\n\\section{Experience}" in rendered
