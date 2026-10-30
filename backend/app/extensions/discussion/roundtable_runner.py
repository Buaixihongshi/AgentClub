"""Host loop for roundtable turns (moved out of the core runner)."""
from backend.app.mcp.tools import MCPTools
from backend.app.memory import handover as handover_store
from backend.app.memory.assembly import assemble, sections_into_prompt
from backend.app.orchestration.context import build_artifact_index, build_conversation_context
from backend.app.orchestration.runner import _build_prompt, _providers_by_agent


def run_roundtable(runner, mission_id):
    from backend.app.extensions.discussion.roundtable import Roundtable
    policy = Roundtable(runner.repository)
    try:
        while True:
            claimed = policy.claim(mission_id)
            if claimed is None:
                return runner.repository.get_mission(mission_id)
            mission, state, step, turn_id, notes = claimed
            agents = {a.id: a for a in runner.agents if a.id in mission.discussion.participant_ids}
            context = build_conversation_context(
                runner.repository, mission.conversation_id, mission_id=mission_id,
                reply_to_turn_id=step.sender_turn_id, agent_registry=runner.agent_registry,
            )
            sections = assemble(runner.repository, runner.settings.memory_root,
                                mission.conversation_id, step.agent_id)
            reply = None
            if step.origin == 'a2a_resume':
                callee_turn = next((e.payload['turn_id'] for e in runner.repository.list_events_after(mission_id, 0)
                                    if e.event_type == 'discussion.turn_claimed' and e.payload.get('step_id') == step.parent_step_id), None)
                reply = '\n'.join(m.content for m in runner.repository.list_messages(mission_id) if m.turn_id == callee_turn and m.role == 'agent')[:1500]
            purpose = '圆桌讨论：仅交流观点，不执行代码修改或自动生成文件。\n' + step.purpose + '\n' + notes + '\n参与者：{}；阶段：{}；已用回合：{}/{}（含当前回合，总结占一回合）。'.format(
                    ', '.join(mission.discussion.participant_ids), state['phase'], state['turns_used'], mission.discussion.max_turns)
            if step.is_closing:
                purpose += '\n当前为总结，不再邀请其他角色。'
            else:
                purpose += ('\n其他参与者已有基础发言安排；只有确需特定接力时才调 invoke_agent。'
                            '需要对方回复后续答时用 mode=wait_reply；接纳后结束当前回合，由宿主安排受邀者和你的续答。'
                            '邀请共享本场总回合预算；工具返回未接纳时继续当前发言，不承诺额外回合。')
            prompt = _build_prompt(mission, purpose, agents[step.agent_id], conversation_context=context,
                                   artifact_index=build_artifact_index(runner.repository, mission_id),
                                   memory_blocks=sections_into_prompt(sections),
                                   a2a_step=step if step.origin != 'workflow' else None, a2a_reply_text=reply,
                                   agents_by_id=agents, max_a2a_invocations=None)
            tools = MCPTools(runner.repository, runner.settings.artifact_root,
                             valid_agent_ids=set(agents), current_step_origin=step.origin)
            outcome = runner._run_turn_with_fallbacks(mission, step.agent_id, turn_id,
                                                   _providers_by_agent(list(agents.values()))[step.agent_id], prompt, tools,
                                                   a2a_sender_turn_id=step.sender_turn_id)
            handover_store.acknowledge(runner.repository, step.agent_id)
            policy.complete(mission_id, turn_id, outcome)
    except Exception:
        policy.fail(mission_id)
        raise

