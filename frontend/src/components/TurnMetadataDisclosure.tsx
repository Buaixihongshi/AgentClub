import type { ThreadEventMetadata, UsageRecord } from "../types";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
  return String(n);
}

export function TurnMetadataDisclosure({
  metadata,
  usage,
}: {
  metadata: ThreadEventMetadata;
  usage?: UsageRecord;
}) {
  const hasDetails =
    metadata.turn_id ||
    metadata.duration_ms ||
    metadata.tool_calls?.length ||
    metadata.handoff_to ||
    metadata.trace_blocks?.length ||
    usage;

  if (!hasDetails) {
    return null;
  }

  return (
    <details className="turn-metadata">
      <summary>运行细节</summary>
      <dl>
        {metadata.turn_id ? (
          <>
            <dt>turn_id</dt>
            <dd>{metadata.turn_id}</dd>
          </>
        ) : null}
        {metadata.duration_ms ? (
          <>
            <dt>duration</dt>
            <dd>{metadata.duration_ms}ms</dd>
          </>
        ) : null}
        {metadata.handoff_to ? (
          <>
            <dt>handoff</dt>
            <dd>{metadata.handoff_to}</dd>
          </>
        ) : null}
      </dl>
      {usage ? (
        <div className="turn-usage-block" data-testid="turn-usage">
          <strong>用量</strong>
          <span>input {formatTokens(usage.input_tokens)}</span>
          <span>cached {formatTokens(usage.cached_input_tokens)}</span>
          <span>output {formatTokens(usage.output_tokens)}</span>
          <span>reasoning {formatTokens(usage.reasoning_output_tokens)}</span>
          {usage.total_cost_usd != null && <span>${usage.total_cost_usd.toFixed(4)}</span>}
        </div>
      ) : null}
      {metadata.tool_calls?.length ? (
        <pre>{JSON.stringify(metadata.tool_calls, null, 2)}</pre>
      ) : null}
      {metadata.trace_blocks?.length ? (
        <pre>{JSON.stringify(metadata.trace_blocks, null, 2)}</pre>
      ) : null}
    </details>
  );
}
