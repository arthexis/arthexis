from __future__ import annotations

import functools
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def _workflow_text() -> str:
    return (REPO_ROOT / ".github" / "workflows" / "issue-intake.yml").read_text(
        encoding="utf-8"
    )


@functools.lru_cache(maxsize=1)
def _intake_script() -> str:
    workflow = yaml.safe_load(_workflow_text())
    return workflow["jobs"]["intake"]["steps"][0]["with"]["script"]


def test_issue_intake_workflow_acknowledges_without_requesting_more_information():
    intake_script = _intake_script()

    assert "addSet.add('triage-ready')" not in intake_script
    assert "addSet.add('needs-info')" not in intake_script
    assert "missingSections" not in intake_script
    assert "Missing details" not in intake_script
    assert "Needs additional details" not in intake_script
    assert "Please update the issue with the missing information" not in intake_script
    assert "Ready to implement or close." in intake_script
    assert "may amend the issue during execution" in intake_script


def test_issue_intake_workflow_uses_existing_repository_label_taxonomy():
    intake_script = _intake_script()

    for stale_label in (
        "type:bug",
        "type:feature",
        "type:question",
        "area:docs",
        "area:install",
        "area:dashboard",
        "area:security",
        "priority:high",
        "needs-triage",
        "triage-ready",
        "new-issue",
    ):
        assert stale_label not in intake_script

    for repo_label in (
        "bug",
        "enhancement",
        "question",
        "documentation",
        "automation",
        "priority: high",
        "priority: critical",
    ):
        assert f"queueLabel('{repo_label}')" in intake_script

    assert "Skipped unavailable labels" not in intake_script
    assert "repoLabelNames.has(name)" in intake_script
    assert "Configured labels not present in repository" in intake_script


def test_issue_intake_workflow_reconciles_conflicting_priority_labels():
    intake_script = _intake_script()
    workflow_text = _workflow_text()

    assert "- labeled" in workflow_text
    assert "const priorityLabelPrefix = 'priority:'" in intake_script
    assert "const desiredPriorityLabel =" in intake_script
    assert (
        "chooseHighestPriorityLabel([...existingPriorityLabels, ...queuedPriorityLabels])"
        in intake_script
    )
    assert "const priorityLabelsToRemove = existingPriorityLabels.filter" in intake_script
    assert "github.rest.issues.removeLabel({ owner, repo, issue_number, name })" in intake_script
    assert "Removed conflicting priority label" in intake_script
    assert "addSet.delete(label)" in intake_script


def test_issue_intake_workflow_reconciles_conflicting_issue_type_labels():
    intake_script = _intake_script()
    workflow_text = _workflow_text()

    assert "- labeled" in workflow_text
    assert "const issueTypeLabels = new Set(['bug', 'enhancement'])" in intake_script
    assert "const isIssueTypeLabel = (name) =>" in intake_script
    assert "const chooseIssueTypeLabel = (labels) =>" in intake_script
    assert "if (hasBugSignal) {" in intake_script
    assert "} else if (hasFeatureSignal) {" in intake_script
    assert "const eventIssueTypeLabel = isIssueTypeLabel(eventLabelName)" in intake_script
    assert "const desiredIssueTypeLabel =" in intake_script
    assert "chooseIssueTypeLabel(existingIssueTypeLabels)" in intake_script
    assert "chooseIssueTypeLabel(queuedIssueTypeLabels)" in intake_script
    assert "const issueTypeLabelsToRemove = existingIssueTypeLabels.filter" in intake_script
    assert "Removed conflicting issue type label" in intake_script


def test_issue_intake_workflow_marks_ocpp_issues_critical():
    intake_script = _intake_script()

    assert "const hasOcppSignal" in intake_script
    assert "'ocpp'" in intake_script
    assert "hasOcppSignal" in intake_script
    assert "hasSecuritySignal" in intake_script
    assert "queueLabel('priority: critical')" in intake_script


def test_issue_intake_workflow_marks_imager_issues_critical():
    intake_script = _intake_script()

    assert "const hasImagerSignal" in intake_script
    assert "/\\bimager\\b/i" in intake_script
    assert "/\\bimage\\s+burn\\b/i" in intake_script
    assert "/\\bburn\\s+image\\b/i" in intake_script
    assert "/\\bgway\\s+image\\b/i" in intake_script
    assert "/\\bbase\\s+image\\b/i" in intake_script
    assert "if (hasOcppSignal || hasImagerSignal || hasSecuritySignal" in intake_script


def test_issue_intake_workflow_ignores_ocpp_template_field_label():
    intake_script = _intake_script()

    assert "bodyWithoutOcppTemplateField" in intake_script
    assert "hasSpecificOcppVersion" in intake_script
    assert "'not sure'" in intake_script
    assert "'not applicable'" in intake_script
    assert "const hasOcppSignal = hasAny(issueText, ['ocpp'" not in intake_script


def test_bug_report_template_explains_ocpp_critical_priority():
    template_text = (REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").read_text(
        encoding="utf-8"
    )

    assert "OCPP reports with a selected protocol version" in template_text
    assert "automatically prioritized as critical" in template_text


def test_issue_intake_workflow_constrains_auth_security_signal():
    intake_script = _intake_script()

    assert "['security', 'vulnerability', 'xss', 'csrf', 'auth']" not in intake_script
    assert "hasRegex(issueText, [/\\bauth\\b/i, /\\bauthn\\b/i, /\\bauthz\\b/i])" in intake_script
    assert "'authentication'" in intake_script
    assert "'authorization'" in intake_script


def test_issue_intake_workflow_does_not_treat_install_as_automation():
    intake_script = _intake_script()

    assert "hasInstallSignal || hasAutomationSignal" not in intake_script
    assert "if (hasAutomationSignal) {" in intake_script
    assert "queueLabel('automation');" in intake_script
    assert "hasBugSignal || hasInstallSignal" in intake_script


def test_issue_intake_workflow_constrains_question_signal():
    intake_script = _intake_script()

    assert "['question', 'how do i', 'how to', 'help']" not in intake_script
    assert "const hasQuestionSignal = hasRegex(issueText" in intake_script
    assert "/\\bquestion\\b/i" in intake_script
    assert "/\\bhelp\\s+(me|with|using|understand|troubleshoot|debug)\\b/i" in intake_script
    assert "operator-facing help" not in intake_script


def test_issue_templates_do_not_prompt_for_unspecified_additional_context():
    template_dir = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
    template_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(template_dir.glob("*.yml"))
        if path.name != "config.yml"
    )

    assert "id: additional" not in template_text
    assert "Additional context" not in template_text
    assert "Alternatives considered" not in template_text
    assert "Suggested update" not in template_text
