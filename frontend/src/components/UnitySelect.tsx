import * as Select from '@radix-ui/react-select';
import { Check, ChevronDown } from 'lucide-react';
import './UnitySelect.css';

/** Shared, source-owned styling around Radix's keyboard and focus behavior. */
export function UnitySelect<T extends string>({ label, value, options, onChange }: {
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return <Select.Root value={value} onValueChange={next => onChange(next as T)}>
    <Select.Trigger className="unity-select" aria-label={label}>
      <Select.Value />
      <Select.Icon asChild><ChevronDown size={15} aria-hidden="true" /></Select.Icon>
    </Select.Trigger>
    <Select.Portal>
      <Select.Content className="unity-select-menu" position="popper" sideOffset={6} collisionPadding={12}>
        <Select.Viewport className="unity-select-options">
          {options.map(option => <Select.Item className="unity-select-option" key={option.value} value={option.value}>
            <Select.ItemText>{option.label}</Select.ItemText>
            <Select.ItemIndicator className="unity-select-check"><Check size={14} aria-hidden="true" /></Select.ItemIndicator>
          </Select.Item>)}
        </Select.Viewport>
      </Select.Content>
    </Select.Portal>
  </Select.Root>;
}
