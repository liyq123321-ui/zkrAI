import type { KeyboardEvent, MouseEvent } from 'react';

export function CopyableWorkItemId({ id, onResult }: {
  id: string;
  onResult: (message: string) => void;
}) {
  const stopCardActivation = (event: MouseEvent<HTMLButtonElement> | KeyboardEvent<HTMLButtonElement>) => {
    event.stopPropagation();
  };

  async function copyId(event: MouseEvent<HTMLButtonElement>) {
    stopCardActivation(event);
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard API unavailable');
      await navigator.clipboard.writeText(id);
      onResult(`已复制 UUID：${id}`);
    } catch {
      onResult('UUID 复制失败');
    }
  }

  return (
    <button
      type="button"
      className="ff-item-id"
      aria-label={`复制工单 UUID：${id}`}
      onClick={(event) => void copyId(event)}
      onKeyDown={stopCardActivation}
    >
      #{id}
    </button>
  );
}
