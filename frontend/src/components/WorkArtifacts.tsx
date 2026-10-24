import { useEffect, useState } from 'react';
import { FileText, Image as ImageIcon, Code2, ChevronRight, ExternalLink } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { artifactName, shortVersion, snapshotUrl } from '../lib/workPresentation';
import { WorkDialog } from './WorkDialog';

export function WorkArtifacts({ conversationId, version, paths }: { conversationId: string; version?: string; paths: string[] }) {
  const [active, setActive] = useState<string | null>(null);
  const priority = (p: string) => /\.html?$/i.test(p) ? 0 : /spec\.md$/.test(p) ? 1 : /host-browser-check\.json$/.test(p) ? 2 : /implementation-check/.test(p) ? 3 : 4;
  const unique = [...new Set(paths)].sort((a, b) => priority(a) - priority(b));
  if (!version || !unique.length) return null;
  const image = unique.find(p => /\.(png|jpe?g|webp)$/i.test(p));
  const render = (path: string) => {
    const html = /\.html?$/i.test(path);
    const photo = /\.(png|jpe?g|webp)$/i.test(path);
    const Icon = photo ? ImageIcon : html ? Code2 : FileText;
    return <button className="work-file" key={path} type="button" onClick={() => setActive(path)}>
      {html && image ? <img loading="lazy" alt="交互预览缩略图" src={snapshotUrl(conversationId, version, image)} /> : <span className="work-file-icon"><Icon size={23} strokeWidth={1.5} /></span>}
      <span><strong>{artifactName(path)}</strong><small>{path.split('.').pop()?.toUpperCase()} · 查看文件</small></span><ChevronRight size={16} />
    </button>;
  };
  return <><div className="work-files">{unique.slice(0, 3).map(render)}</div>
    {unique.length > 3 && <details className="work-more-files"><summary>其余 {unique.length - 3} 份文件</summary><div className="work-files">{unique.slice(3).map(render)}</div></details>}
    {active && <WorkArtifactViewer key={`${version}:${active}`} conversationId={conversationId} version={version} path={active} onClose={() => setActive(null)} />}
  </>;
}
function WorkArtifactViewer({ conversationId, version, path, onClose }: { conversationId: string; version: string; path: string; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [mobile, setMobile] = useState(false);
  const url = snapshotUrl(conversationId, version, path);
  const image = /\.(png|jpe?g|webp|gif)$/i.test(path);
  const html = /\.html?$/i.test(path);
  useEffect(() => {
    if (image || html) return;
    const abort = new AbortController();
    fetch(url, { signal: abort.signal }).then(r => { if (!r.ok) throw new Error('文件暂时无法读取，请稍后重试'); return r.text(); })
      .then(value => { if (!abort.signal.aborted) setText(value); }).catch(e => { if (!abort.signal.aborted) setError(e.message); });
    return () => abort.abort();
  }, [url, image, html]);
  return <WorkDialog wide title={artifactName(path)} onClose={onClose}>
    <div className="work-preview-toolbar"><span>候选快照 {shortVersion(version)}</span><a href={url} target="_blank" rel="noreferrer">打开原文件 <ExternalLink size={14} /></a></div>
    <details className="work-file-source"><summary>文件来源</summary><p>{path}</p><code>{version}</code></details>
    {html && <><div className="work-preview-sizes"><button aria-pressed={!mobile} onClick={() => setMobile(false)}>桌面</button><button aria-pressed={mobile} onClick={() => setMobile(true)}>手机 · 390px</button></div><div className="work-preview-stage"><iframe className={mobile ? 'is-mobile' : ''} title={`方案预览：${path}`} src={url} sandbox="allow-scripts allow-forms" /></div></>}
    {image && <img className="work-full-image" src={url} alt={artifactName(path)} />}
    {!image && !html && (error ? <p role="alert">{error}</p> : text === null ? <p role="status">正在读取文件…</p> : /\.md$/i.test(path) ? <div className="work-document"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ img: ({ alt }) => <span>图片：{alt}</span>, a: ({ children, href }) => <a href={href} target="_blank" rel="noreferrer">{children}</a> }}>{text.slice(0, 200000)}</ReactMarkdown></div> : <pre className="work-code">{text.slice(0, 200000)}</pre>)}
    {text && text.length > 200000 && <p className="work-muted">文件较大，当前展示前 200,000 字符；可打开原文件查看全文。</p>}
  </WorkDialog>;
}
