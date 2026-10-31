import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArtifactViewerModal, useArtifactContent } from './ArtifactPanel';
import { artifactDownloadUrl } from '../lib/api';
import { artifactKindGroups, filterArtifacts, type SidebarArtifact } from '../lib/artifactSidebar';
import { formatAgentName } from '../lib/missionView';
import './ArtifactSidebar.css';

export function ArtifactSidebar({items, onLocate, onPinnedChange}: {
  items: SidebarArtifact[];
  onLocate: (item: SidebarArtifact) => string | undefined;
  onPinnedChange: (pinned: boolean) => void;
}) {
  const [open,setOpen] = useState(false);
  const [wide,setWide] = useState(()=>window.matchMedia('(min-width: 1440px)').matches);
  const [pin,setPin] = useState(()=>{try {return localStorage.getItem('unity.artifactSidebar.pinned') === 'true';}catch{return false;}});
  const [query,setQuery] = useState('');
  const [kind,setKind] = useState('all');
  const [notice,setNotice] = useState('');
  const [active,setActive] = useState<SidebarArtifact>();
  const {content,load} = useArtifactContent();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  const pinned = open && pin && wide;
  useEffect(()=>{
    const media=window.matchMedia('(min-width: 1440px)');
    const change=()=>setWide(media.matches); media.addEventListener('change',change);
    return ()=>media.removeEventListener('change',change);
  },[]);
  useEffect(()=>{onPinnedChange(pinned);},[pinned,onPinnedChange]);
  useEffect(()=>{if(pin && wide) setOpen(true);},[pin,wide]);
  const close=()=>{setOpen(false);trigger.current?.focus();};
  useEffect(()=>{
    if (!open || pinned) return;
    const previous=document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const key=(event:KeyboardEvent)=>{
      if(active) return;
      if(event.key==='Escape') {event.preventDefault();close();}
      if(event.key==='Tab') {
        const nodes=panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input,a[href]');
        if(!nodes?.length)return;
        const first=nodes[0],last=nodes[nodes.length-1];
        if(event.shiftKey && (document.activeElement===first || document.activeElement===panel.current)){event.preventDefault();last.focus();}
        else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first.focus();}
      }
    };
    document.addEventListener('keydown',key);
    return ()=>{document.removeEventListener('keydown',key);previous?.focus();};
  },[open,pinned,active]);
  const visible=filterArtifacts(items,query,kind);
  return <>
    <button ref={trigger} type="button" className={`artifact-tray-trigger ${open?'is-open':''}`} aria-expanded={open} onClick={()=>setOpen(!open)}>产物 <span>{items.length}</span></button>
    {open && !pinned && <button type="button" className="artifact-tray-backdrop" aria-label="关闭产物栏遮罩" onClick={close} />}
    {open && <aside ref={panel} tabIndex={-1} aria-label="交付产物" className={`artifact-sidebar ${pinned?'is-pinned':'is-drawer'}`}>
      <header><strong>交付产物</strong><button type="button" disabled={!wide} aria-pressed={pin} onClick={()=>{const next=!pin;setPin(next);try{localStorage.setItem('unity.artifactSidebar.pinned',String(next));}catch{/* private browsing */}}}>固定</button><button type="button" aria-label="收起产物栏" onClick={close}>×</button></header>
      <p className="artifact-scope-note">当前会话 · 文件已保存不代表已验证</p>
      <input aria-label="搜索产物" placeholder="搜索标题或路径" value={query} onChange={e=>setQuery(e.target.value)} />
      <div className="artifact-kind-filters"><button type="button" aria-pressed={kind==='all'} onClick={()=>setKind('all')}>全部 {items.length}</button>{artifactKindGroups(items).map(group=><button key={group.kind} type="button" aria-pressed={kind===group.kind} onClick={()=>setKind(group.kind)}>{group.kind} {group.count}</button>)}</div>
      <p role="status">{notice}</p>
      <ul className="artifact-sidebar-list">{visible.map(item=><li key={item.identity}>
        <button type="button" className="artifact-sidebar-open" onClick={()=>{setActive(item);load(item.mission_id,item.path ?? '');}}><strong>{item.title}</strong><span>{item.kind ?? 'file'} · {item.agent_id?formatAgentName(item.agent_id):'文件'}</span>{item.path!==item.title && <small>{item.path}</small>}<time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time></button>
        <div><button type="button" onClick={()=>{setNotice(onLocate(item) ?? '已定位');if(!pinned)close();}}>定位</button><a href={artifactDownloadUrl(item.mission_id,item.path ?? '')} download={item.title}>下载</a></div>
      </li>)}</ul>
      {!visible.length && <p>没有匹配的产物</p>}
    </aside>}
    {active && createPortal(<ArtifactViewerModal artifact={active} path={active.path ?? ''} state={content[`${active.mission_id}:${active.path}`]} onClose={()=>setActive(undefined)} />,document.body)}
  </>;
}
