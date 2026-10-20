import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildConversationTimeline } from "../lib/conversationTimeline";
import { buildTurnViews } from "../lib/missionView";
import { TurnTimeline } from "../components/TurnTimeline";
import type { ConversationDetail, MissionDetail, ThreadEvent } from "../types";

const saved: ThreadEvent = {
  id: "artifact:m1:demo.html", mission_id: "m1", event_type: "artifact.saved", role: "system",
  turn_id: "t1", content: "文件已保存，可打开。", created_at: "2026-10-20T07:05:06Z",
  artifact: { id: "m1:demo.html", mission_id: "m1", title: "demo.html", path: "demo.html", kind: "html",
    created_at: "2026-10-20T07:05:06Z" },
};
const latest: MissionDetail = {
  mission: { id: "m1", goal: "demo", status: "running", created_at: "2026-10-20T06:55:33Z", updated_at: saved.created_at },
  messages: [], artifact_events: [saved],
};
const conversation: ConversationDetail = {
  conversation: { id: "c1", title: "demo", status: "active", created_at: latest.mission.created_at, updated_at: saved.created_at },
  missions: [latest.mission], messages: [], artifact_events: [saved],
};

afterEach(() => vi.unstubAllGlobals());

describe("saved-file delivery", () => {
  it("shows the file while the agent is still running, and opens an isolated HTML preview", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      path: "demo.html", content: "<button>演示按钮</button>", kind: "html", total_chars: 25, truncated: false,
    }) }));
    render(<TurnTimeline turns={buildTurnViews(buildConversationTimeline(conversation, latest))} />);
    expect(screen.getAllByLabelText("已保存文件")).toHaveLength(1);
    expect(screen.getByText("文件已保存不代表已验证或任务已完成。")).toBeVisible();
    expect(screen.getByRole("link", { name: "下载文件" })).toHaveAttribute("href", "/api/missions/m1/artifacts/file?path=demo.html");
    fireEvent.click(screen.getByRole("button", { name: /demo.html/ }));
    await waitFor(() => expect(screen.getByTitle("HTML 文件预览")).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledWith("/api/missions/m1/artifacts/content?path=demo.html");
    expect(screen.getByTitle("HTML 文件预览")).toHaveAttribute("sandbox", "allow-scripts");
    expect(screen.getByTitle("HTML 文件预览")).toHaveAttribute("srcdoc", "<button>演示按钮</button>");
    fireEvent.click(screen.getByRole("button", { name: "关闭产物查看器" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("retains a single card through timeout, follow-up and refresh with the source mission link", () => {
    const failure: ThreadEvent = { ...saved, id: "failure:m1:t1", artifact: undefined, event_type: "turn.failure",
      content: "王也本轮超时，未发布回复。", created_at: "2026-10-20T07:05:34Z" };
    const blocked = { ...latest, mission: { ...latest.mission, status: "blocked" as const }, failure_events: [failure] };
    const view = render(<TurnTimeline turns={buildTurnViews(buildConversationTimeline(conversation, blocked))} />);
    expect(screen.getByText(failure.content)).toBeVisible();
    expect(screen.getAllByLabelText("已保存文件")).toHaveLength(1);
    const next = { ...latest, mission: { ...latest.mission, id: "m2" }, artifact_events: [] };
    const refreshed = { ...conversation, failure_events: [failure] };
    view.rerender(<TurnTimeline turns={buildTurnViews(buildConversationTimeline(refreshed, next))} />);
    expect(screen.getAllByLabelText("已保存文件")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "下载文件" })).toHaveAttribute("href", "/api/missions/m1/artifacts/file?path=demo.html");
  });

  it("uses the current snapshot when the same file is saved again", () => {
    const updated = { ...saved, artifact: { ...saved.artifact!, title: "更新的演示" } };
    const events = buildConversationTimeline(conversation, { ...latest, artifact_events: [updated] });
    expect(events).toHaveLength(1);
    expect(events[0].artifact?.title).toBe("更新的演示");
  });
});
