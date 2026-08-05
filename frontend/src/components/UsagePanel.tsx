import type { UsageSummary } from "../types";

const AGENT_NAMES: Record<string, string> = {
  chulan: "张楚岚",
  baobao: "冯宝宝",
  wangye: "王也",
};

function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
  return String(n);
}

export function UsagePanel({ usageSummary }: { usageSummary?: UsageSummary }) {
  if (!usageSummary || !usageSummary.mission_total) {
    return (
      <section className="side-panel usage-panel" aria-label="用量">
        <h2>用量</h2>
        <p className="usage-empty">用量数据：仅真实 CLI 提供（演示任务不计量）</p>
      </section>
    );
  }

  const total = usageSummary.mission_total;

  return (
    <section className="side-panel usage-panel" aria-label="用量">
      <h2>用量</h2>
      <table className="usage-table">
        <thead>
          <tr>
            <th>input</th>
            <th>cached</th>
            <th>output</th>
            <th>reasoning</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>{formatTokens(total.input_tokens)}</td>
            <td>{formatTokens(total.cached_input_tokens)}</td>
            <td>{formatTokens(total.output_tokens)}</td>
            <td>{formatTokens(total.reasoning_output_tokens)}</td>
          </tr>
        </tbody>
      </table>
      {total.total_cost_usd != null && (
        <p className="usage-cost">${total.total_cost_usd.toFixed(4)}</p>
      )}
      {usageSummary.by_agent.length > 0 && (
        <>
          <h3>按 Agent</h3>
          <table className="usage-table usage-agent-table">
            <thead>
              <tr>
                <th>agent</th>
                <th>input</th>
                <th>cached</th>
                <th>output</th>
                <th>reasoning</th>
              </tr>
            </thead>
            <tbody>
              {usageSummary.by_agent.map((a) => (
                <tr key={a.agent_id}>
                  <td>{AGENT_NAMES[a.agent_id] ?? a.agent_id}</td>
                  <td>{formatTokens(a.input_tokens)}</td>
                  <td>{formatTokens(a.cached_input_tokens)}</td>
                  <td>{formatTokens(a.output_tokens)}</td>
                  <td>{formatTokens(a.reasoning_output_tokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
