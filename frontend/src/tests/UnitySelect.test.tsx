import { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { UnitySelect } from '../components/UnitySelect';

const originalScroll = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView');
beforeAll(() => Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() }));
afterAll(() => {
  if (originalScroll) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', originalScroll);
  else delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView;
});
function Filter() {
  const [value, setValue] = useState('all');
  return <UnitySelect label="筛选任务" value={value} onChange={setValue} options={[{ value: 'all', label: '全部任务' }, { value: 'active', label: '进行中' }, { value: 'attention', label: '待处理' }, { value: 'done', label: '已结束' }]} />;
}

describe('Unity 筛选下拉框', () => {
  it('支持键盘选择并将焦点返回筛选框', async () => {
    const user = userEvent.setup();
    render(<Filter />);
    const trigger = screen.getByRole('combobox', { name: '筛选任务' });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    await screen.findByRole('listbox');
    await user.keyboard('{End}{Enter}');
    await waitFor(() => expect(trigger).toHaveTextContent('已结束'));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
  it('Esc 关闭时保留原选择', async () => {
    const user = userEvent.setup();
    render(<Filter />);
    const trigger = screen.getByRole('combobox', { name: '筛选任务' });
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    await screen.findByRole('listbox');
    await user.keyboard('{End}{Escape}');
    await waitFor(() => expect(screen.queryByRole('listbox')).not.toBeInTheDocument());
    expect(trigger).toHaveTextContent('全部任务');
  });
});
