type Mermaid = typeof import("mermaid").default;

let loader: Promise<Mermaid> | null = null;
let sequence = 0;

/** 按需加载 mermaid（体积大，只在消息里真的出现图表时才拉取），严格模式渲染为 SVG。 */
export async function renderMermaid(source: string): Promise<string> {
  loader ??= import("mermaid").then(({ default: mermaid }) => {
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral", fontFamily: "inherit" });
    return mermaid;
  }).catch((error) => {
    loader = null;
    throw error;
  });
  const mermaid = await loader;
  // parse 先校验语法，避免 render 失败时在 body 上残留错误节点。
  await mermaid.parse(source);
  const { svg } = await mermaid.render(`chat-mermaid-${++sequence}`, source);
  return svg;
}
