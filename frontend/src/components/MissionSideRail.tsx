import { useMemo, useRef, useState } from "react";
import { GitBranch } from "lucide-react";
import { buildTraceView } from "../lib/traceView";
import type { ProviderStatusState } from "../hooks/useProviderStatus";
import type { MissionDetail, OnboardingView } from "../types";
import { TraceDialog } from "./TraceDialog";

export function MissionSideRail({ detail }: { detail?: MissionDetail; providerStatus?: ProviderStatusState; onboarding?: OnboardingView | null }) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const trace = useMemo(() => buildTraceView(detail), [detail]);

  function close() {
    setOpen(false);
    requestAnimationFrame(() => buttonRef.current?.focus());
  }

  return (
    <aside className="mission-side-rail" aria-label="任务侧栏">
      <button
        ref={buttonRef}
        className="trace-launcher"
        type="button"
        onClick={() => setOpen(true)}
        aria-label="打开工具与协作追踪"
        aria-haspopup="dialog"
        title="工具与协作追踪"
      >
        <GitBranch size={22} strokeWidth={2.2} aria-hidden="true" />
        <span>追踪</span>
        <small>{trace.callCount} 次调用</small>
      </button>
      {open ? <TraceDialog detail={detail} trace={trace} onClose={close} /> : null}
    </aside>
  );
}
