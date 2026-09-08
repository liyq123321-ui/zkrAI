import type { ReactNode } from 'react';
import { FileText } from 'lucide-react';
import { CopyableWorkItemId } from './CopyableWorkItemId';
import type { WorkItemDto } from './dto';
import { validWorkItemSummary } from './workItemSummary';

export function RootWorkItemDetail({ item, onCopyResult, children }: {
  item: WorkItemDto;
  onCopyResult: (message: string) => void;
  children: ReactNode;
}) {
  const summary = validWorkItemSummary(item.summary) || item.title || item.id;
  const detail = item.title || item.objective || item.description || '未提供项目需求详情';

  return (
    <article className="ff-root-detail" aria-label="项目需求信息">
      <div className="ff-spec-titlebar ff-root-detail-titlebar">
        <div className="ff-spec-title-tags">
          <CopyableWorkItemId id={item.id} onResult={onCopyResult} />
          <span className="ff-priority">P0</span>
        </div>
        <div>
          <h2>{summary}</h2>
          <p>Root · 项目需求</p>
        </div>
      </div>

      <section className="ff-spec-objective ff-root-detail-description">
        <span><FileText aria-hidden="true" /></span>
        <div>
          <h3>详情</h3>
          <p>{detail}</p>
        </div>
      </section>

      <div className="ff-root-detail-prd">{children}</div>
    </article>
  );
}
