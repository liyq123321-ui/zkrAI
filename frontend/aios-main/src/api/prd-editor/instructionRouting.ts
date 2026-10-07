export function isWorkbenchCommand(message: string): boolean {
  if (/后端接口|源文件|代码文件|其他项目/.test(message)) return false;
  if (/^(?:确认|记录)业务决定[：:]|^保存(?:本次|本文档|项目)编辑偏好[：:]/.test(message.trim())) return true;
  return (/会议包/.test(message) && /生成|整理|导出|比较/.test(message))
    || (/收集|整理/.test(message) && /待确认|信息篮|信息收集篮/.test(message))
    || (/批注/.test(message) && /加入|放入|收进/.test(message) && /篮|待确认/.test(message))
    || /第[一二三四五六七八九十\d]+(?:个|条)?问题(?:的)?(?:答案|答复)(?:是|为|[:：])/.test(message)
    || /(?:答案|回答).*(?:预览|写入)|(?:预览|写入).*已确认/.test(message);
}

export function prdWorkbenchIntent(message: string): 'impact' | 'health' | 'issues' | 'alternatives' | 'meeting' | 'questions' | 'anchors' | 'tasks' | 'policies' | 'performance' | null {
  if (!/打开|查看|显示|收集|整理|对照|生成/.test(message) || /修改.{0,8}(?:代码|源文件)|后端接口/.test(message)) return null;
  if (/需求影响地图/.test(message)) return 'impact';
  if (/文档健康地图/.test(message)) return 'health';
  if (/待确认信息|信息收集篮/.test(message)) return 'questions';
  if (/两套方案|方案对照草稿/.test(message)) return 'alternatives';
  if (/评审会议包/.test(message)) return 'meeting';
  if (/问题闭环/.test(message)) return 'issues';
  if (/候选任务|后台任务|待应用方案/.test(message)) return 'tasks';
  if (/稳定位置/.test(message)) return 'anchors';
  if (/决定卡|业务决定|编辑偏好|决定与偏好/.test(message)) return 'policies';
  if (/执行耗时|耗时记录|性能记录|缓存命中/.test(message)) return 'performance';
  return null;
}

/** The open page supplies context, but an explicit other target wins. */
export function isPrdRevisionInstruction(message: string, prdOpen: boolean): boolean {
  const text = message.trim();
  if (prdOpen && (isWorkbenchCommand(text) || prdWorkbenchIntent(text))) return true;
  if (prdOpen && /^(?:再简短|再精简|再详细|继续修改|沿用|保持刚才|按刚才|撤销|撤回)|(?:上一条|刚才|上个方案).*只(?:应用|保留|采纳)/.test(text)) return true;
  const prdTarget = /(?:\bPRD\b|产品需求文档|需求文档|当前文档|文档里|章节|段落|这一部分|这部分|这一段|这一块|配图|图片|插画|插图|结构图|架构图|关系图|流程图|状态图|时序图|实体关系图|ER\s*图|Mermaid|draw\.?io|表格|目录|小节)/i.test(text);
  const otherTarget = /(?:代码|源文件|前端页面|后端接口|后端服务|数据库表|迁移脚本|看板|工单|Agent\s*Spec|顶层图|任务流转图|审计记录)/i.test(text);
  const explicitPrdArea = /(?:PRD|文档)\s*(?:的|中|里|内|上)(?:的)?\s*.{0,40}(?:章节|部分|内容|说明|条款|图|接口|流程)/i.test(text);
  const edit = /(?:修改|修订|调整|补充|增加|添加|删除|移除|重写|改写|完善|更新|替换|精简|扩写|润色|重新生成|重画|绘制|画出|画一张|画个|画图|做一张|做个|制作|生成|移动|移到|挪到|放到|排到|排序|重排|调换|交换|升为|降为|上移|下移|前移|后移|提前|改成子节|改为子节|改|写)/.test(text);
  if (/^(?:请|帮我|现在)?\s*(?:重新)?(?:生成|创建|编写|审核|提交|批准|通过|修订)(?:当前|整份)?\s*PRD$/i.test(text.replace(/[。！!？?，,]/g, ''))) return false;
  const documentVisual = /(?:ER\s*图|实体关系图|Mermaid|draw\.?io|架构图|流程图|状态图|时序图|配图|图片|插画|插图)/i.test(text);
  if (!edit || (otherTarget && !explicitPrdArea && !documentVisual)) return false;
  return prdTarget || prdOpen;
}

/** An explicitly named child is the target; its named parent only locates it. */
export function prdRevisionTargets<T extends { id: string; title: string; parent_id: string | null }>(
  blocks: T[], instruction: string, focusedBlockId?: string | null, selectedBlockId?: string | null,
): T[] {
  const named = blocks.filter(block => block.title && instruction.includes(block.title));
  const byId = new Map(blocks.map(block => [block.id, block]));
  const specific = named.filter(block => !named.some(other => {
    let parentId = other.parent_id;
    while (parentId) {
      if (parentId === block.id) return true;
      parentId = byId.get(parentId)?.parent_id ?? null;
    }
    return false;
  }));
  if (specific.length) return specific.sort((left, right) => instruction.indexOf(left.title) - instruction.indexOf(right.title));
  const fallback = blocks.find(block => block.id === focusedBlockId)
    ?? blocks.find(block => block.id === selectedBlockId);
  return fallback ? [fallback] : [];
}

export interface ScreenshotAnchor {
  anchor_text: string;
  heading: string;
  visual_context?: string;
}

export interface ScreenshotLocation {
  blockId: string;
  startLine: number;
  endLine: number;
  anchorText: string;
}

/** Only an exact, unique text match may drive a screenshot-based edit. */
export function locateScreenshotInPrd<T extends { id: string; title: string; content: string }>(
  blocks: T[], anchor: ScreenshotAnchor,
): ScreenshotLocation {
  const normalize = (value: string) => value.replace(/[\s\p{P}\p{S}]/gu, '').toLocaleLowerCase();
  const needle = normalize(anchor.anchor_text);
  if (needle.length < 8) throw new Error('截图中没有识别出足够清晰的正文，请截取包含完整段落的画面，或在右侧选中目标段落。');
  const matches: ScreenshotLocation[] = [];
  for (const block of blocks) {
    const chars: string[] = [];
    const offsets: number[] = [];
    for (let index = 0; index < block.content.length; index += 1) {
      const char = block.content[index];
      if (normalize(char)) { chars.push(char.toLocaleLowerCase()); offsets.push(index); }
    }
    const haystack = chars.join('');
    let from = 0;
    while (from < haystack.length) {
      const found = haystack.indexOf(needle, from);
      if (found < 0) break;
      const start = offsets[found];
      const end = offsets[found + needle.length - 1];
      matches.push({
        blockId: block.id,
        startLine: block.content.slice(0, start).split('\n').length,
        endLine: block.content.slice(0, end).split('\n').length,
        anchorText: anchor.anchor_text,
      });
      from = found + 1;
    }
  }
  const heading = normalize(anchor.heading);
  const narrowed = heading
    ? matches.filter(match => blocks.some(block => block.id === match.blockId && normalize(block.title).includes(heading)))
    : [];
  const candidates = narrowed.length ? narrowed : matches;
  if (candidates.length !== 1) {
    throw new Error(candidates.length
      ? '截图文字在 PRD 中出现多次，请在右侧选中目标段落后重新发送。'
      : '截图文字与当前 PRD 不匹配，请截取当前版本中更完整的段落，或在右侧选中目标段落。');
  }
  return candidates[0];
}
