import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { Archive, ArchiveRestore, ArrowLeft, Pencil, Plus, Trash2 } from "lucide-react";
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
  onDelete?: (id: string) => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
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
            <Plus size={14} aria-hidden="true" />
            新对话
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
                      <Pencil size={13} aria-hidden="true" />
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
                    {props.scope === "active" ? (
                      <Archive size={13} aria-hidden="true" />
                    ) : (
                      <ArchiveRestore size={13} aria-hidden="true" />
                    )}
                  </button>
                  {props.onDelete ? (
                    <button
                      type="button"
                      className="conv-archive-btn conv-delete-btn"
                      aria-label="删除会话"
                      title="删除"
                      onClick={(event) => {
                        event.stopPropagation();
                        setDeletingId(conv.id);
                      }}
                    >
                      <Trash2 size={13} aria-hidden="true" />
                    </button>
                  ) : null}
                </div>
              ) : null}
              {deletingId === conv.id && props.onDelete ? (
                <div className="conv-delete-confirm" role="alertdialog" aria-label={`删除会话「${conv.title}」`}>
                  <span>删除后消息和记录无法恢复</span>
                  <button
                    type="button"
                    className="conv-delete-confirm-btn"
                    onClick={() => {
                      setDeletingId(null);
                      props.onDelete?.(conv.id);
                    }}
                  >
                    删除
                  </button>
                  <button type="button" onClick={() => setDeletingId(null)}>取消</button>
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
          {props.scope === "active" ? (
            <Archive size={14} aria-hidden="true" />
          ) : (
            <ArrowLeft size={14} aria-hidden="true" />
          )}
          {props.scope === "active" ? "查看归档" : "返回会话"}
        </button>
      </div>
    </nav>
  );
}
