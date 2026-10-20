from backend.app.evaluation.adapters import _stage_from_prompt


def test_stage_uses_current_turn_purpose_not_history():
    prompt = (
        "最近对话：\n"
        "P2：检查一：初稿漏了约束。\n"
        "Mission: demo\n"
        "Goal: demo\n"
        "Turn purpose: 检查二：换角度复查边界条件。"
    )

    assert _stage_from_prompt(prompt) == "check2"


def test_stage_recognises_dynamic_a2a_before_purpose_words():
    prompt = (
        "最近对话：\n"
        "P2：检查二：ok。\n"
        "P1，P3（在 turn 1）请你接力：复核\n"
        "Turn purpose: 复核一个边界条件。"
    )

    assert _stage_from_prompt(prompt) == "dynamic"


def test_stage_defaults_to_final_for_single_agent_turns():
    prompt = "Mission: demo\nGoal: demo\nTurn purpose: 独立完成用户请求。"
    assert _stage_from_prompt(prompt) == "final"
