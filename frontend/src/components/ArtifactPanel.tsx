import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { artifactDownloadUrl, fetchArtifactContent } from "../lib/api";
import type { Artifact, ArtifactContent } from "../types";
import { formatAgentName } from "../lib/missionView";

type ContentState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: ArtifactContent };

const MARKDOWN_KINDS = new Set(["md", "markdown", "mdx", "txt", "text"]);

export function ArtifactPanel({
  missionId,
  artifacts,
  variant = "panel",
}: {
  missionId?: string;
  artifacts: Artifact[];
  variant?: "panel" | "chat";
}) {
  const [activePath, setActivePath] = useState<string | null>(null);
  const [contentByPath, setContentByPath] = useState<Record<string, ContentState>>({});

  const closeViewer = () => setActivePath(null);

  useEffect(() => {
    if (!activePath) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeViewer();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [activePath]);

  const openViewer = (path: string) => {
    if (!path || !missionId) {
      return;
    }
    setActivePath(path);
    if (contentByPath[path]) {
      return;
    }
    setContentByPath((prev) => ({ ...prev, [path]: { status: "loading" } }));
    fetchArtifactContent(missionId, path)
      .then((data) =>
        setContentByPath((prev) => ({ ...prev, [path]: { status: "ready", data } })),
      )
      .catch((error: unknown) =>
        setContentByPath((prev) => ({
          ...prev,
          [path]: { status: "error", message: error instanceof Error ? error.message : "加载产物内容失败" },
        })),
      );
  };

  const activeArtifact = activePath
    ? artifacts.find((artifact) => (artifact.path ?? artifact.file_path ?? "") === activePath)
    : undefined;
  const activeState = activePath ? contentByPath[activePath] : undefined;

  return (
    <section className={variant === "chat" ? "saved-artifact-card" : "side-panel"} aria-label={variant === "chat" ? "已保存文件" : "产物"}>
      {variant === "chat" ? (
        <header className="saved-artifact-header">
          <strong>文件已保存，可打开</strong>
          <span>文件已保存不代表已验证或任务已完成。</span>
        </header>
      ) : <h2>产物</h2>}
      {artifacts.length === 0 ? (
        <p>暂无产物</p>
      ) : (
        <ul className="artifact-list">
          {artifacts.map((artifact) => {
            const path = artifact.path ?? artifact.file_path ?? "";
            const selected = activePath === path;
            const showPath = Boolean(path) && path !== artifact.title;
            return (
              <li key={artifact.id} className={selected ? "artifact-item artifact-item--open" : "artifact-item"}>
                <button
                  type="button"
                  className="artifact-item-toggle"
                  aria-expanded={selected}
                  onClick={() => openViewer(path)}
                  disabled={!path || !missionId}
                >
                  <span className="artifact-item-disclosure" aria-hidden="true">
                    {selected ? "▾" : "▸"}
                  </span>
                  <span className="artifact-item-main">
                    <span className="artifact-item-head">
                      <strong className="artifact-item-title">{artifact.title}</strong>
                      <span className="artifact-kind-badge">{artifact.kind ?? "file"}</span>
                    </span>
                    {(artifact.agent_id || showPath) ? (
                      <span className="artifact-item-sub">
                        {artifact.agent_id ? (
                          <span className="artifact-agent-chip">{formatAgentName(artifact.agent_id)}</span>
                        ) : null}
                        {showPath ? <span className="artifact-item-path">{path}</span> : null}
                      </span>
                    ) : null}
                  </span>
                </button>
                {path && missionId ? (
                  <a className="artifact-download" href={artifactDownloadUrl(missionId, path)} download={artifact.title}>
                    下载文件
                  </a>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {activeArtifact && activePath
        ? createPortal(
          <ArtifactViewerModal
            artifact={activeArtifact}
            path={activePath}
            state={activeState}
            onClose={closeViewer}
          />,
          document.body,
        )
        : null}
    </section>
  );
}

function ArtifactViewerModal({
  artifact,
  path,
  state,
  onClose,
}: {
  artifact: Artifact;
  path: string;
  state?: ContentState;
  onClose: () => void;
}) {
  const titleId = "artifact-viewer-title";
  return (
    <div className="artifact-modal-backdrop" onClick={onClose}>
      <div
        className="artifact-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="artifact-modal-header">
          <div>
            <p className="artifact-modal-kicker">产物查看器</p>
            <h2 id={titleId}>{artifact.title}</h2>
            <div className="artifact-modal-meta">
              <span>{artifact.kind ?? "file"}</span>
              {artifact.agent_id ? <span>{formatAgentName(artifact.agent_id)}</span> : null}
              {artifact.turn_id ? <span>{artifact.turn_id}</span> : null}
              <span>{path}</span>
            </div>
          </div>
          <button
            type="button"
            className="artifact-modal-close"
            aria-label="关闭产物查看器"
            onClick={onClose}
          >
            ×
          </button>
        </header>
        <div className="artifact-modal-body">
          {state?.status === "loading" || !state ? (
            <p className="artifact-content-status">加载中…</p>
          ) : null}
          {state?.status === "error" ? (
            <p className="artifact-content-status runtime-error">{state.message}</p>
          ) : null}
          {state?.status === "ready" ? <ArtifactBody data={state.data} /> : null}
        </div>
      </div>
    </div>
  );
}

function ArtifactBody({ data }: { data: ArtifactContent }) {
  const isMarkdown = MARKDOWN_KINDS.has((data.kind ?? "").toLowerCase());
  const isHtml = ["html", "htm"].includes((data.kind ?? "").toLowerCase()) && !data.truncated;
  return (
    <>
      {data.truncated ? (
        <p className="artifact-content-status">内容较大，仅显示前 {data.content.length} 字符</p>
      ) : null}
      {isHtml ? (
        <iframe title="HTML 文件预览" className="artifact-html-preview" sandbox="allow-scripts" srcDoc={data.content} />
      ) : isMarkdown ? (
        <div className="bubble-md artifact-md">
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkBreaks]}
            components={{
              a: ({ node: _node, ...props }) => (
                <a {...props} target="_blank" rel="noopener noreferrer" />
              ),
            }}
          >
            {data.content}
          </ReactMarkdown>
        </div>
      ) : (
        <pre className="artifact-pre">
          <code>{data.content}</code>
        </pre>
      )}
    </>
  );
}
