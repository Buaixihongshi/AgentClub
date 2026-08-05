import { useCallback, useEffect, useState } from "react";

export interface ProviderRow {
  id: string;
  label: string;
  enabled: boolean;
  available: boolean;
  version?: string | null;
  error?: string | null;
}

export interface ProviderStatusState {
  providers: ProviderRow[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

type ProviderApiPayload = Array<{
  id: string;
  label: string;
  enabled: boolean;
  available: boolean;
  version?: string | null;
  error?: string | null;
}>;

export function useProviderStatus(): ProviderStatusState {
  const [providers, setProviders] = useState<ProviderRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/providers");
      if (!response.ok) throw new Error("加载 Provider 状态失败");
      const data = (await response.json()) as ProviderApiPayload;
      setProviders(
        data
          .filter((p) => p.id !== "fake" && p.id !== "gemini")
          .map((p) => ({
          id: p.id,
          label: p.label,
          enabled: p.enabled,
          available: p.available,
          version: p.version ?? undefined,
          error: p.error ?? undefined,
          })),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = useCallback(() => {
    void load();
  }, [load]);

  return { providers, loading, error, refresh };
}
