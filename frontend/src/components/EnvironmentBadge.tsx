import type { RuntimeIdentity, SourceIdentity } from "../types";

const LABELS: Record<string, string> = {
  local: "LOCAL", development: "DEV", test: "TEST", alpha: "ALPHA", runtime: "RUNTIME",
};

function short(sha: string | null | undefined) {
  return sha ? sha.slice(0, 7) : "未知";
}

export function EnvironmentBadge({ runtime, loading = false, frontend = __FRONTEND_IDENTITY__ }: {
  runtime?: RuntimeIdentity;
  loading?: boolean;
  frontend?: SourceIdentity;
}) {
  const started = runtime?.started;
  const known = Boolean(frontend.git_sha && frontend.source_digest && started?.git_sha && started.source_digest);
  const differentSource = known && (frontend.git_sha !== started?.git_sha || frontend.source_digest !== started?.source_digest);
  const differentCheckout = Boolean(frontend.checkout_id && started?.checkout_id && frontend.checkout_id !== started.checkout_id);
  const differentEnvironment = Boolean(frontend.environment && started?.environment && frontend.environment !== started.environment);
  const changed = runtime?.source_changed === true;
  const warning = differentSource || differentCheckout || differentEnvironment || changed;
  const environment = started?.environment ?? frontend.environment ?? "local";

  return (
    <details className={`environment-identity${warning ? " environment-identity-warning" : ""}`}>
      <summary>
        <span>{LABELS[environment] ?? environment}</span>
        <span>前端 {short(frontend.git_sha)}{frontend.dirty ? "*" : ""} · 后端 {short(started?.git_sha)}{started?.dirty ? "*" : ""}</span>
        <span>{loading ? "正在核对版本" : !runtime || !known ? "版本信息不完整" : warning ? "环境需核对" : "启动版本一致"}</span>
      </summary>
      <div className="environment-identity-detail">
        <p>前端启动/构建：{frontend.git_sha ?? "未知"} · {frontend.captured_at ?? "未记录时间"}</p>
        <p>后端初始化：{started?.git_sha ?? "未知"} · {started?.captured_at ?? "未记录时间"}</p>
        {started ? <p>PID {started.pid} · Python {started.python} · 演示开关{started.use_fake_cli ? "开启" : "关闭"}（实际 Provider 以回合记录为准）</p> : null}
        {frontend.dirty || started?.dirty ? <p>* 启动时包含未提交修改。</p> : null}
        {!runtime && !loading ? <p>后端尚未提供启动身份；旧服务的运行版本无法由磁盘提交推断。</p> : null}
        {runtime && !known ? <p>无法取得完整源码身份，暂不能确认版本一致。</p> : null}
        {differentSource ? <p role="alert">前后端启动源码不同，请核对访问入口及启动版本。</p> : null}
        {differentCheckout ? <p role="alert">前后端来自不同工作目录，请核对代理目标。</p> : null}
        {differentEnvironment ? <p role="alert">前后端环境标签不同，请核对启动配置。</p> : null}
        {changed ? <p role="alert">磁盘源码在后端初始化后发生变化；当前后端仍保留启动身份。</p> : null}
        <p>前端身份是启动/构建快照；热更新后需结合磁盘变化核对。终端可运行 scripts/env.sh status 查看目录和数据路径。</p>
      </div>
    </details>
  );
}
