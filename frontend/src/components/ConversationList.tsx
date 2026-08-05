import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import type { ConversationSummary } from "../types";

export function ConversationList(props: {
  conversations: ConversationSummary[];
  selectedId?: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  scope: "active" | "archived";
  onScopeChange: (scope: "active" | "archived") => void;
  onStatusChange: (id: string, status: "active" | "archived") => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingId && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editingId]);

  function beginEdit(event: MouseEvent, conv: ConversationSummary) {
    event.stopPropagation();
    event.preventDefault();
    setEditingId(conv.id);
    setDraft(conv.title);
  }

  function commitEdit(conv: ConversationSummary) {
    const trimmed = draft.trim();
    setEditingId(null);
    if (!trimmed || trimmed === conv.title) return;
    props.onRename(conv.id, trimmed);
  }

  function cancelEdit() {
    setEditingId(null);
  }

  function handleInputKey(event: KeyboardEvent<HTMLInputElement>, conv: ConversationSummary) {
    if (event.key === "Enter") {
      event.preventDefault();
      commitEdit(conv);
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelEdit();
    }
  }

  return (
    <nav className="conversation-list" aria-label="会话列表">
      <div className="conversation-list-header">
        <h2>{props.scope === "archived" ? "已归档" : "会话"}</h2>
        {props.scope === "active" ? (
          <button className="conv-new-btn" onClick={props.onNew}>
            + 新对话
          </button>
        ) : null}
      </div>
      <ul className="conv-items">
        {props.conversations.map((conv) => {
          const isEditing = editingId === conv.id;
          return (
            <li key={conv.id} className="conv-item-row">
              <button
                className={`conv-item ${conv.id === props.selectedId ? "conv-item-active" : ""}`}
                onClick={() => {
                  if (isEditing) return;
                  props.onSelect(conv.id);
                }}
              >
                <span className="conv-item-title-row">
                  {isEditing ? (
                    <input
                      ref={inputRef}
                      className="conv-item-title-input"
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      onKeyDown={(event) => handleInputKey(event, conv)}
                      onBlur={() => commitEdit(conv)}
                      onClick={(event) => event.stopPropagation()}
                      onDoubleClick={(event) => event.stopPropagation()}
                      aria-label="编辑会话标题"
                    />
                  ) : (
                    <strong
                      onDoubleClick={(event) => beginEdit(event, conv)}
                      title="双击重命名"
                    >
                      {conv.title}
                    </strong>
                  )}
                </span>
                <span className="conv-item-meta">
                  {conv.mission_count > 0
                    ? `${conv.mission_count} 轮任务`
                    : "空会话"}
                </span>
                {conv.latest_message_preview ? (
                  <span className="conv-item-preview">{conv.latest_message_preview}</span>
                ) : null}
              </button>
              {!isEditing ? (
                <div className="conv-row-actions">
                  {props.scope === "active" ? (
                    <button
                      type="button"
                      className="conv-rename-btn"
                      aria-label="重命名会话"
                      title="重命名"
                      onClick={(event) => beginEdit(event, conv)}
                    >
                      ✎
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="conv-archive-btn"
                    aria-label={props.scope === "active" ? "归档会话" : "恢复会话"}
                    title={props.scope === "active" ? "归档" : "恢复"}
                    onClick={(event) => {
                      event.stopPropagation();
                      props.onStatusChange(conv.id, props.scope === "active" ? "archived" : "active");
                    }}
                  >
                    {props.scope === "active" ? "⌑" : "↩"}
                  </button>
                </div>
              ) : null}
            </li>
          );
        })}
        {props.conversations.length === 0 ? (
          <li className="conv-empty">{props.scope === "archived" ? "暂无归档会话" : "暂无会话"}</li>
        ) : null}
      </ul>
      <div className="conversation-list-footer">
        <button
          type="button"
          className="conv-scope-btn"
          onClick={() => props.onScopeChange(props.scope === "active" ? "archived" : "active")}
        >
          <span aria-hidden="true">{props.scope === "active" ? "⌑" : "←"}</span>
          {props.scope === "active" ? "查看归档" : "返回会话"}
        </button>
      </div>
    </nav>
  );
}
