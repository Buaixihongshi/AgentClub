import { useMemo, useState } from "react";
import type { ThoughtEntry } from "../lib/missionView";
import type { TurnToolCall } from "../types";

function Chevron({ expanded }: { expanded: boolean }) {
  return (
    <svg
      aria-hidden="true"
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="thought-chevron"
      style={{ transform: expanded ? "rotate(90deg)" : "rotate(0deg)" }}
    >
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

function BrainIcon() {
  return (
    <svg
      aria-hidden="true"
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="thought-brain"
    >
      <path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z" />
      <path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z" />
      <path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4" />
    </svg>
  );
}

const PREVIEW_LEN = 56;

type MergedItem =
  | { kind: "thought"; entry: ThoughtEntry; createdAt: string }
  | { kind: "tool"; entry: TurnToolCall; createdAt: string };

function summarizeToolInput(input?: Record<string, unknown>): string | undefined {
  if (!input) return undefined;
  const keys = Object.keys(input);
  if (keys.length === 0) return undefined;
  const parts: string[] = [];
  for (const key of keys.slice(0, 3)) {
    const value = input[key];
    if (typeof value === "string") {
      parts.push(`${key}="${value.length > 24 ? `${value.slice(0, 24)}…` : value}"`);
    } else if (typeof value === "number" || typeof value === "boolean") {
      parts.push(`${key}=${value}`);
    } else if (value !== null && value !== undefined) {
      parts.push(key);
    }
  }
  return parts.join(" ");
}

export function ThoughtBlock({
  thoughts = [],
  toolCalls = [],
}: {
  thoughts?: ThoughtEntry[];
  toolCalls?: TurnToolCall[];
}) {
  const [open, setOpen] = useState(false);
  const [includeTools, setIncludeTools] = useState(false);

  const hasTools = toolCalls.length > 0;

  const mergedItems = useMemo<MergedItem[]>(() => {
    const items: MergedItem[] = thoughts.map((entry) => ({
      kind: "thought",
      entry,
      createdAt: entry.createdAt,
    }));
    if (includeTools) {
      for (const tool of toolCalls) {
        items.push({ kind: "tool", entry: tool, createdAt: tool.createdAt });
      }
    }
    items.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    return items;
  }, [thoughts, toolCalls, includeTools]);

  const firstContent = thoughts[0]?.content ?? "";
  const preview =
    firstContent.length > PREVIEW_LEN
      ? `${firstContent.slice(0, PREVIEW_LEN)}…`
      : firstContent;

  return (
    <div className="thought-block">
      <div className="thought-toggle-row">
        <button
          type="button"
          className="thought-toggle"
          aria-expanded={open}
          aria-label={open ? "折叠思考过程" : "展开思考过程"}
          onClick={() => setOpen((value) => !value)}
        >
          <Chevron expanded={open} />
          <BrainIcon />
          <span className="thought-label">思考过程</span>
          <span className="thought-count">· {thoughts.length} 条</span>
          {!open && preview && (
            <span className="thought-preview">{preview}</span>
          )}
        </button>
        {hasTools && (
          <button
            type="button"
            className={`thought-tools-toggle${includeTools ? " is-on" : ""}`}
            aria-pressed={includeTools}
            onClick={(e) => {
              e.stopPropagation();
              setIncludeTools((v) => !v);
              if (!open) setOpen(true);
            }}
          >
            {includeTools ? "纯思考" : `含工具 · ${toolCalls.length}`}
          </button>
        )}
      </div>
      {open && (
        <ul className="thought-list">
          {thoughts.length === 0 && (
            <li className="thought-item">本轮未记录思考摘要。</li>
          )}
          {mergedItems.map((item, index) => {
            if (item.kind === "thought") {
              const thought = item.entry;
              return (
                <li
                  className="thought-item"
                  key={`thought-${thought.createdAt}-${index}`}
                >
                  {thought.kind ? (
                    <span className="thought-kind">{thought.kind}</span>
                  ) : (
                    <span
                      className="thought-kind thought-kind-empty"
                      aria-hidden="true"
                    />
                  )}
                  <span className="thought-content">{thought.content}</span>
                </li>
              );
            }
            const tool = item.entry;
            const inputSummary = summarizeToolInput(tool.toolInput);
            return (
              <li
                className="thought-item thought-item-tool"
                key={`tool-${tool.createdAt}-${index}`}
              >
                <span className="thought-kind thought-kind-tool">tool</span>
                <span className="thought-content">
                  <code className="thought-tool-name">{tool.toolName}</code>
                  {tool.summary && (
                    <span className="thought-tool-summary"> {tool.summary}</span>
                  )}
                  {!tool.summary && inputSummary && (
                    <span className="thought-tool-summary"> {inputSummary}</span>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
