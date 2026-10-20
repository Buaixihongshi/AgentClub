import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  findActiveMention,
  getMentionSuggestions,
  replaceActiveMention,
  type MentionOption,
} from "../lib/mentions";

export interface ComposerInterruptTarget {
  agentId: string;
  label: string;
}

export function MissionComposer(props: {
  value: string;
  busy: boolean;
  hasActiveMission: boolean;
  disabled?: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
  submitLabel?: string;
  submitDisabled?: boolean;
  placeholder?: string;
  /** Roundtable mode routes replies through target_agent_id, so @ autocomplete is off. */
  mentionsEnabled?: boolean;
  interruptTargets?: ComposerInterruptTarget[];
  interruptTargetId?: string | null;
  onInterruptTargetChange?: (agentId: string | null) => void;
}) {
  const [suggestions, setSuggestions] = useState<MentionOption[]>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const mentionsEnabled = props.mentionsEnabled !== false;
  const interruptTargets = props.interruptTargets ?? [];
  const showInterruptTargets = interruptTargets.length > 0 && Boolean(props.onInterruptTargetChange);

  useEffect(() => {
    if (!menuOpen) {
      setSuggestions([]);
      setHighlighted(0);
    }
  }, [menuOpen]);

  function handleChange(newValue: string) {
    props.onChange(newValue);
    if (!mentionsEnabled) {
      setMenuOpen(false);
      return;
    }
    const cursor = textareaRef.current?.selectionStart ?? newValue.length;
    const active = findActiveMention(newValue, cursor);
    if (active) {
      const results = getMentionSuggestions(active.query);
      setSuggestions(results);
      setMenuOpen(results.length > 0);
      setHighlighted(0);
    } else {
      setMenuOpen(false);
    }
  }

  function selectOption(option: MentionOption) {
    const cursor = textareaRef.current?.selectionStart ?? props.value.length;
    const { newValue, newCursor } = replaceActiveMention(props.value, cursor, option);
    props.onChange(newValue);
    setMenuOpen(false);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(newCursor, newCursor);
    });
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (menuOpen) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setHighlighted((i) => (i + 1) % suggestions.length);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setHighlighted((i) => (i - 1 + suggestions.length) % suggestions.length);
      } else if (event.key === "Enter") {
        event.preventDefault();
        selectOption(suggestions[highlighted]);
      } else if (event.key === "Escape") {
        event.preventDefault();
        setMenuOpen(false);
      }
      return;
    }

    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing &&
      props.value.trim() &&
      !props.busy &&
      !props.disabled &&
      !props.submitDisabled
    ) {
      event.preventDefault();
      props.onSubmit();
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!props.value.trim() || props.busy || props.disabled || props.submitDisabled) return;
    props.onSubmit();
  }

  const submitLabel =
    props.submitLabel ?? (props.hasActiveMission ? "发送插话" : "创建任务");

  return (
    <form className="mission-composer" onSubmit={submit}>
      {showInterruptTargets ? (
        <div className="composer-interrupt-target">
          <label htmlFor="interrupt-target">点名先回应（可选）</label>
          <select
            id="interrupt-target"
            value={props.interruptTargetId ?? ""}
            disabled={props.disabled || props.busy}
            onChange={(event) =>
              props.onInterruptTargetChange?.(event.target.value || null)
            }
          >
            <option value="">不指定，按顺序自然回应</option>
            {interruptTargets.map((target) => (
              <option key={target.agentId} value={target.agentId}>
                {target.label}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <label htmlFor="mission-input">任务输入</label>
      <div className="composer-input-wrapper">
        <textarea
          ref={textareaRef}
          id="mission-input"
          value={props.value}
          disabled={props.disabled}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={props.disabled ? "归档会话只读；恢复后可继续对话" : props.placeholder ?? (mentionsEnabled ? "输入任务，@ 选择 Agent · Enter 发送" : "聊点什么，Enter 发送 · Shift + Enter 换行")}
          rows={2}
          role="combobox"
          aria-expanded={menuOpen}
          aria-controls="mention-listbox"
          aria-activedescendant={menuOpen ? `mention-opt-${highlighted}` : undefined}
        />
        {menuOpen && (
          <ul id="mention-listbox" role="listbox" className="mention-menu" aria-label="Agent 候选">
            {suggestions.map((opt, idx) => (
              <li
                key={opt.id}
                id={`mention-opt-${idx}`}
                role="option"
                aria-selected={idx === highlighted}
                className={`mention-option ${idx === highlighted ? "mention-option-active" : ""}`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  selectOption(opt);
                }}
              >
                <strong>{opt.label}</strong>
                <span>{opt.role}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <button
        type="submit"
        disabled={props.disabled || props.busy || props.submitDisabled || !props.value.trim()}
      >
        {submitLabel}
      </button>
    </form>
  );
}
