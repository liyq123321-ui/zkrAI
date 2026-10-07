// @vitest-environment jsdom
import { useState } from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { RecoveryNotice, useDraftRecovery } from './draftRecovery';
import { clearSession } from '../../auth/session';

function Editor() {
  const [text, setText] = useState('');
  const recovery = useDraftRecovery('doc:one', text ? { text, revision: 3 } : null);
  return <><RecoveryNotice recovery={recovery} onRestore={value => setText(value.text)} /><input aria-label="draft" value={text} onChange={e => setText(e.target.value)} /></>;
}
beforeEach(() => { localStorage.clear(); localStorage.setItem('ff_auth_user', JSON.stringify({ username: 'alice' })); });
afterEach(cleanup);

it('keeps unsaved text across remount and only restores after explicit action', () => {
  const first = render(<Editor />);
  fireEvent.change(screen.getByLabelText('draft'), { target: { value: 'unsaved paragraph' } });
  first.unmount(); render(<Editor />);
  expect((screen.getByLabelText('draft') as HTMLInputElement).value).toBe('');
  fireEvent.click(screen.getByText('恢复到输入区'));
  expect((screen.getByLabelText('draft') as HTMLInputElement).value).toBe('unsaved paragraph');
});

it('isolates users and clears local recovery on sign-out', () => {
  const first = render(<Editor />);
  fireEvent.change(screen.getByLabelText('draft'), { target: { value: 'alice only' } });
  first.unmount();
  localStorage.setItem('ff_auth_user', JSON.stringify({ username: 'bob' }));
  render(<Editor />);
  expect(screen.queryByText('恢复到输入区')).toBeNull();
  clearSession();
  expect(Object.keys(localStorage).some(k => k.startsWith('ff-prd-recovery:'))).toBe(false);
});
