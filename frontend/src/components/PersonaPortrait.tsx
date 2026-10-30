import type { ReactNode } from "react";
import chulan from "../assets/portraits/chulan.png";
import baobao from "../assets/portraits/baobao.png";
import wangye from "../assets/portraits/wangye.png";

const portraits: Record<string, string> = { chulan, baobao, wangye };

/** 头像框沿用各入口的样式，自定义角色保留原来的文字头像。 */
export function PersonaPortrait({ agentId, children }: { agentId?: string | null; children: ReactNode }) {
  const src = agentId ? portraits[agentId] : undefined;
  return src ? <img className="persona-portrait" src={src} alt="" draggable={false} /> : <>{children}</>;
}
