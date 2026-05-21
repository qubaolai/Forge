from __future__ import annotations

from forge.chat.assembler import _merge_user_prompt


def test_merge_user_prompt_prefers_both_when_present() -> None:
    merged = _merge_user_prompt(
        "agent rules",
        "workspace rules",
        {"shell_timeout_seconds": 30},
    )
    assert "agent rules" in merged
    assert "workspace rules" in merged
    assert "Workspace Rules" in merged
    assert "Workspace Settings" in merged
    assert '"shell_timeout_seconds": 30' in merged


def test_merge_user_prompt_handles_missing_values() -> None:
    assert _merge_user_prompt("", "workspace rules").endswith("workspace rules")
    assert _merge_user_prompt("agent rules", "") == "agent rules"
    assert _merge_user_prompt("", "", {"a": 1}).startswith("## Workspace Settings")
    assert _merge_user_prompt("", "") == ""
