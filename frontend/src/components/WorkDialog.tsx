import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

export function WorkDialog({ title, children, onClose, wide = false, className = '' }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean; className?: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const dialog = ref.current;
    const previous = document.activeElement as HTMLElement | null;
    if (dialog && !dialog.open) {
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
    }
    return () => { dialog?.close?.(); if (previous?.isConnected) previous.focus(); };
  }, []);
  return createPortal(<dialog ref={ref} className={`work-dialog${wide ? ' work-dialog-wide' : ''} ${className}`.trim()} aria-labelledby={id}
    onCancel={e => { e.preventDefault(); onClose(); }}>
    <header className="work-dialog-header"><h2 id={id}>{title}</h2><button type="button" aria-label={`关闭${title}`} onClick={onClose}><X size={20} /></button></header>
    <div className="work-dialog-content">{children}</div>
  </dialog>, document.body);
}
