"""Restricted MCP surface for closed-book cost pilots."""
from backend.app.mcp import server
from backend.app.domain.models import OrchestrationMode


def allowed(ctx):
    names = {"publish_message", "read_context", "log_thought"}
    mission = ctx.tools.repository.get_mission(ctx.mission_id)
    if mission and mission.orchestration and mission.orchestration.mode == OrchestrationMode.DYNAMIC:
        names.add("invoke_agent")
    return names


def list_tools(ctx, params):
    result = server._handle_tools_list(ctx, params)
    result["tools"] = [tool for tool in result["tools"] if tool["name"] in allowed(ctx)]
    ctx.tools.repository.add_event(ctx.mission_id, "evaluation.tools_list", {
        "turn_id": ctx.turn_id,
        "agent_id": ctx.agent_id,
        "tools": result["tools"],
    })
    return result


def call_tool(ctx, params):
    if params.get("name") not in allowed(ctx):
        return {"content": [{"type": "text", "text": "Tool disabled by closed-book evaluation policy"}], "isError": True}
    return server._handle_tools_call(ctx, params)


if __name__ == "__main__":
    server._REQUEST_HANDLERS["tools/list"] = list_tools
    server._REQUEST_HANDLERS["tools/call"] = call_tool
    raise SystemExit(server.run())
