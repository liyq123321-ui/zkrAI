import { ReactNode, useEffect, useRef } from 'react';
import { X } from 'lucide-react';

export function WorkItemDialog({ title, children, onClose }: {
  title: string; children: ReactNode; onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialog.showModal();
    return () => {
      dialog.close();
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, []);
  return <dialog ref={ref} aria-label={title} onCancel={(event) => { event.preventDefault(); onClose(); }}
    className="m-auto max-h-[92vh] w-[calc(100%-2rem)] max-w-6xl overflow-y-auto rounded-2xl border border-slate-700 bg-slate-950 p-0 text-slate-100 shadow-2xl backdrop:bg-black/70">
    <header className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-slate-800 bg-slate-950 px-5 py-4">
      <h2 className="font-semibold">{title}</h2>
      <button type="button" autoFocus aria-label="关闭详情" onClick={onClose} className="rounded-lg p-2 text-slate-300 hover:bg-slate-800"><X className="h-5 w-5" /></button>
    </header>
    <div className="p-4">{children}</div>
  </dialog>;
}
