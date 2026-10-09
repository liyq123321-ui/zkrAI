import { diffLines } from './BlockHistory';

/**
 * 「对比上一版」的正文高亮。
 *
 * 约束：本项目只有 `react-markdown` + `remark-gfm`，**没有 `rehype-raw`**，
 * 所以 markdown 里的内联 `<del>`/`<ins>` 不会被解析成元素。
 * 因此高亮只能用**原生 markdown 原语**表达，再由渲染层覆盖组件翻译成想要的样式：
 *   - 旧内容 → `~~文本~~`（GFM 删除线）→ 渲染层上红色删除线
 *   - 新内容 → `[文本](#ff-ins)`（伪链接）→ 渲染层翻译成 `<ins>`
 *
 * 为什么不整行包 `~~`：被包住的行**仍然含有 `|`**，remark-gfm 照样把它当表格行
 * （实测：行数、列数都不变），但整行文字会被**挤进第一个单元格**、其余格子空着，
 * 管道符还会当正文显示出来。所以分两步：先做**行级** diff；成对的删/改行再做
 * **token 级行内** diff，只把真正变化的片段包起来，`|` 留在未改动片段里原样输出。
 * 整行新增/删除且该行是表格行时，退而求其次：**逐单元格**包裹，高亮才落在各自的格子里。
 */

/** 新增片段的载体协议；渲染层据此把链接翻译成 `<ins>`。 */
export const INSERT_HREF = '#ff-ins';

export interface HighlightSegment {
  kind: 'same' | 'add' | 'remove';
  text: string;
}

const TOKEN = /[A-Za-z0-9_]+|\s+|[^A-Za-z0-9_\s]/g;

/**
 * 切分粒度：ASCII 单词/数字串整体成一个 token，其余（含中文）逐字符。
 *
 * 中文必须逐字，否则「协同」→「协作」会被当作两个毫无关系的词，
 * 变成"整词删 + 整词加"，而不是只标出真正变化的那一个字。
 */
export function tokenize(text: string): string[] {
  return text.match(TOKEN) ?? [];
}

const INLINE_BUDGET = 200_000;

/** token 级 LCS；相邻同类片段会合并，避免碎成一堆单字。 */
export function diffTokens(before: string, after: string): HighlightSegment[] {
  const a = tokenize(before);
  const b = tokenize(after);
  if (a.length * b.length > INLINE_BUDGET) {
    return [
      ...(before ? [{ kind: 'remove' as const, text: before }] : []),
      ...(after ? [{ kind: 'add' as const, text: after }] : []),
    ];
  }
  const dp = Array.from({ length: a.length + 1 }, () => new Uint32Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const rows: HighlightSegment[] = [];
  const push = (kind: HighlightSegment['kind'], text: string) => {
    if (!text) return;
    const last = rows[rows.length - 1];
    if (last && last.kind === kind) last.text += text;
    else rows.push({ kind, text });
  };
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      push('same', a[i]);
      i++;
      j++;
    } else if (j < b.length && (i === a.length || dp[i][j + 1] > dp[i + 1][j])) {
      push('add', b[j]);
      j++;
    } else {
      push('remove', a[i]);
      i++;
    }
  }
  return rows;
}

const NEEDS_ESCAPE = /[\\`*_[\]<>|~#]/g;
const ESCAPED = /\\([\\`*_[\]<>|~#])/g;

/** 把字面文本转义成"在 markdown 里原样显示"的形式。 */
export function escapeMarkdown(text: string): string {
  return text.replace(NEEDS_ESCAPE, '\\$&');
}

/**
 * 先还原既有转义再重新转义。
 *
 * 导入时单元格里的 `|` 已被写成 `\|`；直接再转义会得到 `\\\|`，显示成 `\|` 而不是 `|`。
 * 变化的片段一律按**字面文本**处理最安全 —— 它已经被 `~~` 或链接包住，
 * 里面的 markdown 记号不应再被解释成结构。
 */
function safeInline(text: string): string {
  return escapeMarkdown(text.replace(ESCAPED, '$1'));
}

function inlineMarkdown(segments: HighlightSegment[]): string {
  return segments.map(segment => {
    if (segment.kind === 'same') return segment.text;
    const text = safeInline(segment.text);
    return segment.kind === 'remove' ? `~~${text}~~` : `[${text}](${INSERT_HREF})`;
  }).join('');
}

/** 按未转义的 `|` 拆分表格行，首尾空片段一并返回。 */
export function splitCells(line: string): string[] {
  const cells: string[] = [];
  let current = '';
  for (let index = 0; index < line.length; index++) {
    const char = line[index];
    if (char === '\\' && index + 1 < line.length) {
      current += char + line[++index];
      continue;
    }
    if (char === '|') {
      cells.push(current);
      current = '';
      continue;
    }
    current += char;
  }
  cells.push(current);
  return cells;
}

export function isTableRow(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.length > 1 && trimmed.startsWith('|') && trimmed.endsWith('|');
}

/** 整行增删时逐单元格包裹 —— 保住表格结构，否则这一整行的 `|` 被包住后整张表会断。 */
function wrapTableRow(line: string, kind: 'add' | 'remove'): string {
  const trimmed = line.trim();
  const cells = splitCells(trimmed);
  const marked = cells.map(cell => {
    const lead = cell.match(/^\s*/)?.[0] ?? '';
    const trail = cell.match(/\s*$/)?.[0] ?? '';
    const body = cell.slice(lead.length, cell.length - trail.length);
    if (!body) return cell;
    const text = safeInline(body);
    return `${lead}${kind === 'remove' ? `~~${text}~~` : `[${text}](${INSERT_HREF})`}${trail}`;
  });
  return marked.join('|');
}

/** 整行删除/新增的呈现形式。 */
function wholeLine(line: string, kind: 'add' | 'remove'): string {
  if (isTableRow(line)) return wrapTableRow(line, kind);
  const text = safeInline(line);
  return kind === 'remove' ? `~~${text}~~` : `[${text}](${INSERT_HREF})`;
}

/**
 * 生成「带改动高亮」的 markdown。
 *
 * 未改动时原样返回 —— 绝大多数章节走这条路径，渲染结果与普通图文阅读完全一致。
 */
export function markdownWithVersionDiff(before: string, after: string): string {
  if (before === after) return after;
  const rows = diffLines(before, after);
  const lines: string[] = [];
  let index = 0;
  while (index < rows.length) {
    if (rows[index].kind === 'same') {
      lines.push(rows[index].text);
      index++;
      continue;
    }
    // 一段连续的改动：删掉的行与新增的行按顺序配对，配对上的做行内 diff。
    const removed: string[] = [];
    const added: string[] = [];
    while (index < rows.length && rows[index].kind !== 'same') {
      (rows[index].kind === 'remove' ? removed : added).push(rows[index].text);
      index++;
    }
    const paired = Math.min(removed.length, added.length);
    for (let k = 0; k < paired; k++) lines.push(inlineMarkdown(diffTokens(removed[k], added[k])));
    for (let k = paired; k < removed.length; k++) lines.push(wholeLine(removed[k], 'remove'));
    for (let k = paired; k < added.length; k++) lines.push(wholeLine(added[k], 'add'));
  }
  return lines.join('\n');
}

/** 逐块算出「改动高亮版正文」与「这一版新增的块」。 */
export function highlightByBlock(previous: Map<string, string>, current: Map<string, string>) {
  const highlighted = new Map<string, string>();
  const added = new Set<string>();
  for (const [id, content] of current) {
    const before = previous.get(id);
    if (before === undefined) {
      added.add(id);
      continue;
    }
    if (before !== content) highlighted.set(id, markdownWithVersionDiff(before, content));
  }
  return { highlighted, added };
}
