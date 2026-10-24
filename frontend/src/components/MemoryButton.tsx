import { useCallback, useRef, useState } from "react";
import { Brain } from "lucide-react";
import { MemoryModal } from "./MemoryModal";

interface Props {
  conversationId?: string | null;
}

export function MemoryButton({ conversationId }: Props) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const handleClose = useCallback(() => {
    setOpen(false);
    setTimeout(() => buttonRef.current?.focus(), 0);
  }, []);

  return (
    <>
      <button
        ref={buttonRef}
        className="settings-button"
        data-testid="memory-button"
        aria-label="打开记忆管理"
        title="记忆管理"
        onClick={() => setOpen(true)}
      >
        <Brain size={18} strokeWidth={2} aria-hidden="true" />
      </button>
      {open && <MemoryModal conversationId={conversationId ?? null} onClose={handleClose} />}
    </>
  );
}
