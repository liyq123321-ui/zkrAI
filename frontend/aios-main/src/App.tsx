import { lazy, Suspense } from 'react';
import { ApiWorkspace } from './api/ApiWorkspace';
import { appConfig } from './api/config';

const MockApp = lazy(() => import('./MockApp'));

function LoadingScreen({ label }: { label: string }) {
  return (
    <main className="min-h-screen grid place-items-center bg-slate-950 text-slate-100">
      <div className="rounded-2xl border border-slate-800 bg-slate-900 px-8 py-6 shadow-xl">
        {label}
      </div>
    </main>
  );
}

export default function App() {
  if (appConfig.dataMode === 'api') {
    return <ApiWorkspace />;
  }

  return (
    <Suspense fallback={<LoadingScreen label="正在加载 Mock 演示数据…" />}>
      <MockApp />
    </Suspense>
  );
}
