import { useEffect, useState } from "react";
import type { MissionDetail } from "../types";
import { missionStreamUrl } from "../lib/api";

export type StreamState = "idle" | "connecting" | "open" | "reconnecting" | "closed";

export function useMissionStream(
  missionId: string | undefined,
  onSnapshot: (detail: MissionDetail) => void
): StreamState {
  const [state, setState] = useState<StreamState>("idle");

  useEffect(() => {
    if (!missionId) {
      setState("idle");
      return;
    }

    setState("connecting");
    const source = new EventSource(missionStreamUrl(missionId));

    source.addEventListener("open", () => setState("open"));
    source.addEventListener("snapshot", (event) => {
      try {
        onSnapshot(JSON.parse(event.data) as MissionDetail);
        setState("open");
      } catch {
        setState("reconnecting");
      }
    });
    source.addEventListener("error", () => setState("reconnecting"));

    return () => {
      source.close();
      setState("closed");
    };
  }, [missionId, onSnapshot]);

  return state;
}
