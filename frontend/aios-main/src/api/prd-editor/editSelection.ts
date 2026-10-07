import type { Block } from './types';

/** Keep operations on the same chapter, dependencies and structural moves together. */
export function editGroups(steps: Record<string, unknown>[], blocks: Block[], coupled = false): number[][] {
  if (coupled || steps.some(step => step.action === 'move_block')) return [steps.map((_, i) => i)];
  const groups: number[][] = [];
  for (let index = 0; index < steps.length; index++) {
    const id = String(steps[index].block_id);
    const independent = (other: Record<string, unknown>) => {
      const step = steps[index];
      if (step.action === 'replace_text' && other.action === 'replace_text') {
        const a = String(step.old_text ?? ''), b = String(other.old_text ?? '');
        return !!a && !!b && !a.includes(b) && !b.includes(a) && !String(step.new_text ?? '').includes(b) && !String(other.new_text ?? '').includes(a);
      }
      if (step.action === 'table_set' && other.action === 'table_set' && Number(step.row) > 0 && Number(other.row) > 0 && !step.row_match && !other.row_match)
        return step.table_index !== other.table_index || step.row !== other.row || step.column !== other.column;
      return false;
    };
    const related = (otherId: string, other: Record<string, unknown>) => (id === otherId && !independent(other))
      || blocks.find(b => b.id === id)?.dependencies.includes(otherId)
      || blocks.find(b => b.id === otherId)?.dependencies.includes(id);
    const connected = groups.filter(group => group.some(i => related(String(steps[i].block_id), steps[i])));
    const merged = [index, ...connected.flat()];
    connected.forEach(group => groups.splice(groups.indexOf(group), 1));
    groups.push(merged.sort((a, b) => a - b));
  }
  return groups.sort((a, b) => a[0] - b[0]);
}
