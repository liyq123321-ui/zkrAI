import type { Block } from './types';

export type VersionChange = 'same' | 'added' | 'removed' | 'structure' | 'content';
export interface ComparedBlock {
  id: string;
  before?: Block;
  after?: Block;
  change: VersionChange;
}

export interface AlignedVersionRow {
  before?: Block;
  after?: Block;
}

// Keep each snapshot's own reading order while matching common chapters on a
// shared row. Insertions and removals leave a blank cell on the other side.
export function alignVersionBlocks(before: Block[], after: Block[]): AlignedVersionRow[] {
  const lengths = Array.from({ length: before.length + 1 }, () => new Uint32Array(after.length + 1));
  for (let i = before.length - 1; i >= 0; i--) {
    for (let j = after.length - 1; j >= 0; j--) {
      lengths[i][j] = before[i].id === after[j].id
        ? lengths[i + 1][j + 1] + 1 : Math.max(lengths[i + 1][j], lengths[i][j + 1]);
    }
  }
  const rows: AlignedVersionRow[] = [];
  let i = 0, j = 0;
  while (i < before.length || j < after.length) {
    if (i < before.length && j < after.length && before[i].id === after[j].id) {
      rows.push({ before: before[i++], after: after[j++] });
    } else if (j < after.length && (i === before.length || lengths[i][j + 1] > lengths[i + 1][j])) {
      rows.push({ after: after[j++] });
    } else {
      rows.push({ before: before[i++] });
    }
  }
  return rows;
}

// Compare the order of blocks that survived in both snapshots. An inserted
// sibling should not make every later sibling look moved.
function siblingOrderChanged(block: Block, own: Block[], other: Block[]): boolean {
  const otherIds = new Set(other.map(item => item.id));
  const ownPeers = own.filter(item => item.parent_id === block.parent_id && otherIds.has(item.id));
  const otherPeers = other.filter(item => item.parent_id === block.parent_id && ownPeers.some(peer => peer.id === item.id));
  return ownPeers.findIndex(item => item.id === block.id) !== otherPeers.findIndex(item => item.id === block.id);
}

export function compareVersionBlocks(before: Block[], after: Block[]): ComparedBlock[] {
  const oldById = new Map(before.map(block => [block.id, block]));
  const newById = new Map(after.map(block => [block.id, block]));
  return [...before.map(block => {
    const newer = newById.get(block.id);
    if (!newer) return { id: block.id, before: block, change: 'removed' as const };
    const structureChanged = block.title !== newer.title || block.parent_id !== newer.parent_id
      || siblingOrderChanged(block, before, after);
    return { id: block.id, before: block, after: newer,
      change: structureChanged ? 'structure' as const : block.content !== newer.content ? 'content' as const : 'same' as const };
  }), ...after.filter(block => !oldById.has(block.id)).map(block => ({ id: block.id, after: block, change: 'added' as const }))];
}

export function versionBlockDepth(block: Block, blocks: Block[]): number {
  const byId = new Map(blocks.map(item => [item.id, item]));
  const visited = new Set<string>([block.id]);
  let depth = 0;
  let parentId = block.parent_id;
  while (parentId && byId.has(parentId) && !visited.has(parentId)) {
    visited.add(parentId);
    depth++;
    parentId = byId.get(parentId)!.parent_id;
  }
  return Math.min(depth, 6);
}
