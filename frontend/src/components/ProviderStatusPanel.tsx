import type { ProviderStatusState } from "../hooks/useProviderStatus";
import type { OnboardingView } from "../types";

export function ProviderStatusPanel({
  providers,
  loading,
  error,
  refresh,
  onboarding,
}: ProviderStatusState & { onboarding?: OnboardingView | null }) {
  const visibleProviders = providers.filter(
    (provider) => provider.id !== "fake" && provider.id !== "gemini",
  );

  return (
    <section className="provider-status-panel" aria-label="Provider 状态">
      <div className="provider-status-header">
        <h2>Provider 状态</h2>
        <button
          className="provider-refresh-btn"
          onClick={refresh}
          disabled={loading}
          aria-label="刷新 Provider 状态"
        >
          {loading ? "加载中…" : "刷新"}
        </button>
      </div>
      {error ? (
        <p className="provider-status-error" role="alert">{error}</p>
      ) : null}
      {visibleProviders.length === 0 && !loading ? (
        <p className="provider-status-empty">暂无 Provider 数据</p>
      ) : null}
      <ul className="provider-status-list">
        {visibleProviders.map((p) => {
          const onboardingProvider = onboarding?.providers.find((item) => item.id === p.id);
          const verificationFailed =
            onboarding?.verification_provider_id === p.id &&
            onboarding.state === "provider_verification_failed";
          const lifecycle = onboardingProvider?.verified
            ? { label: "已验证", dot: "dot-ok" }
            : verificationFailed
              ? { label: "验证失败", dot: "dot-warn" }
              : !p.available
                ? { label: "未安装", dot: "dot-off" }
                : p.enabled
                  ? { label: "已安装 · 待验证", dot: "dot-warn" }
                  : { label: "已安装 · 未启用", dot: "dot-warn" };
          return (
            <li key={p.id} className="provider-status-item">
              <span
                className={`provider-status-dot ${lifecycle.dot}`}
                aria-label={lifecycle.label}
              />
              <span className="provider-status-identity">
                <span className="provider-status-name">{p.label}</span>
                <span className="provider-status-lifecycle">{lifecycle.label}</span>
              </span>
              {p.version ? (
                <span className="provider-status-version">{p.version}</span>
              ) : null}
              {p.error ? (
                <span className="provider-status-err-detail">{p.error}</span>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
