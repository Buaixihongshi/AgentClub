import type { OnboardingState, OnboardingView } from "../types";

const STEP_STATE: Record<OnboardingState, number> = {
  ready_for_first_chat: 2,
  first_chat_running: 2,
  first_chat_failed: 2,
  first_chat_succeeded: 3,
  provider_verifying: 3,
  provider_verification_failed: 3,
  provider_verified: 3,
  team_intro_running: 4,
  team_intro_failed: 4,
  team_intro_completed: 4,
  completed: 5,
};

export function OnboardingProgress({ onboarding }: { onboarding: OnboardingView | null }) {
  const current = onboarding ? STEP_STATE[onboarding.state] : 1;
  const rows = [
    { step: 1, label: "启动项目" },
    { step: 2, label: "叫醒一个角色" },
    { step: 3, label: "配置真实 CLI" },
    { step: 4, label: "认识完整团队" },
  ];

  return (
    <section className="side-panel onboarding-progress" aria-label="启动进度">
      <h2>启动进度</h2>
      <ol>
        {rows.map((row) => {
          const status = row.step < current ? "已完成" : row.step === current ? "当前" : "未开始";
          return (
            <li key={row.step} className={row.step === current ? "is-current" : row.step < current ? "is-complete" : ""}>
              <span>{row.step}</span>
              <strong>{row.label}</strong>
              <small>{status}</small>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
