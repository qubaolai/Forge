from __future__ import annotations

from pathlib import Path

import pytest

from forge.orchestration.workflow import TemplateLoader, TriageClassifier


def test_template_loader_loads_builtin_templates():
    loader = TemplateLoader()
    templates = loader.load_all(refresh=True)

    assert "question_only" in templates
    assert "quick_fix" in templates
    assert "regression_test" in templates
    assert "investigation" in templates
    assert len(templates["quick_fix"].phases) == 3
    assert templates["quick_fix"].mode == "light"
    assert templates["investigation"].mode == "heavy"


def test_template_loader_get_unknown_raises():
    loader = TemplateLoader()
    with pytest.raises(KeyError):
        loader.get("not-exists")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("帮我跑一下回归测试", "regression_test"),
        ("这个 bug 需要 quick fix", "quick_fix"),
        ("解释一下这个函数用途", "question_only"),
    ],
)
def test_triage_classifier(message: str, expected: str):
    triage = TriageClassifier()
    decision = triage.classify(message)
    assert decision.template_id == expected


def test_template_yaml_triage_keywords_loaded(tmp_path: Path, monkeypatch):
    templates_root = tmp_path / "templates"
    templates_root.mkdir(parents=True, exist_ok=True)
    (templates_root / "_default.yaml").write_text(
        "triage:\n  default_template_id: question_only\n",
        encoding="utf-8",
    )
    (templates_root / "question_only.yaml").write_text(
        "\n".join(
            [
                "id: question_only",
                "name: Question",
                "description: qa",
                "triage:",
                '  any_keyword: ["解释"]',
                "  priority: 10",
                "phases:",
                "  - id: p1",
                "    role: triage",
                "    task: q",
            ]
        ),
        encoding="utf-8",
    )
    template_path = templates_root / "quick_fix.yaml"
    template_path.write_text(
        "\n".join(
            [
                "id: quick_fix",
                "name: Quick",
                "description: qf",
                "triage:",
                '  any_keyword: ["爆红"]',
                "  priority: 80",
                "phases:",
                "  - id: p1",
                "    role: developer",
                "    task: fix",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKFLOW_TEMPLATES_DIR", str(templates_root))

    loader = TemplateLoader()
    triage = TriageClassifier(template_loader=loader)
    first = triage.classify("CI 爆红了")
    assert first.template_id == "quick_fix"

    # 改模板关键词后, classify() 会 refresh, 立刻生效.
    template_path.write_text(
        "\n".join(
            [
                "id: quick_fix",
                "name: Quick",
                "description: qf",
                "triage:",
                '  any_keyword: ["线上告警"]',
                "  priority: 80",
                "phases:",
                "  - id: p1",
                "    role: developer",
                "    task: fix",
            ]
        ),
        encoding="utf-8",
    )
    second = triage.classify("线上告警需要排查")
    assert second.template_id == "quick_fix"
