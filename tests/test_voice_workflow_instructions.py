from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_plan_access_is_resolved_before_asking_for_interview_level():
    for relative in (
        ".agents/skills/backend-internship-interviewer/SKILL.md",
        ".agents/skills/test-backend-interview/SKILL.md",
    ):
        workflow = (ROOT / relative).read_text(encoding="utf-8").lower()
        assert "chatgpt_plan_status" in workflow
        assert "before asking for internship" in workflow
        assert "after this succeeds" in workflow or "after plan access and model setup succeed" in workflow


def test_voice_turns_do_not_start_speech_while_tool_call_is_running():
    workflow = (ROOT / ".agents/skills/backend-internship-interviewer/SKILL.md").read_text(encoding="utf-8").lower()
    test_workflow = (ROOT / ".agents/skills/test-backend-interview/SKILL.md").read_text(encoding="utf-8").lower()
    instructions = (ROOT / "tools/voice_probe.py").read_text(encoding="utf-8").lower()

    for source in (workflow, test_workflow, instructions):
        assert "silent" in source and "while the tool call runs" in source
        assert "utterance" in source
        assert "playback" in source
