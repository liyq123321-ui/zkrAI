"""Validate explicit object picks against saved content before and after planning."""
import re
from difflib import SequenceMatcher
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from app.services.block_service import BlockError


class ObjectTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["cell", "row", "column", "mermaid"]
    block_id: str
    block_version: int = Field(ge=1)
    table_index: int = Field(default=0, ge=0)
    headers: list[str] = Field(default_factory=list, max_length=100)
    key_column: str = Field(default="", max_length=500)
    key_value: str = Field(default="", max_length=4000)
    column: str = Field(default="", max_length=500)
    diagram_index: int = Field(default=0, ge=0)
    source: str = Field(default="", max_length=30000)
    object_id: str = Field(default="", max_length=200)
    label: str = Field(default="", max_length=1000)


def table_data(content):
    from app.services.document_edit_service import _table_ranges, _cells
    lines = content.splitlines()
    return [[_cells(lines[start]), *[_cells(line) for line in lines[start + 2:end]]]
            for start, end in _table_ranges(lines)]


def resolve_target(source, target):
    from app.services.document_edit_service import _MERMAID_FENCE
    block = next((b for b in source if b['id'] == target.block_id), None)
    if not block or block['version'] != target.block_version:
        raise BlockError('所选对象的来源版本已变化，请重新选择', 409)
    if target.kind == 'mermaid':
        diagrams = list(_MERMAID_FENCE.finditer(block['content']))
        if target.diagram_index >= len(diagrams) or diagrams[target.diagram_index].group(0).split('\n', 1)[1].rsplit('```', 1)[0].strip() != target.source.strip():
            raise BlockError('图源已变化，请重新选择图中对象', 409)
        if target.object_id and not re.search(r'(?<![\w-])' + re.escape(target.object_id) + r'(?![\w-])', target.source):
            raise BlockError('图中对象 ID 不存在', 422)
        return {'diagram_index': target.diagram_index}
    tables = table_data(block['content'])
    if target.table_index >= len(tables): raise BlockError('表格已不存在', 409)
    table = tables[target.table_index]
    if table[0] != target.headers or len(set(table[0])) != len(table[0]):
        raise BlockError('表头变化或重复，请重新选择', 409)
    row = None
    if target.kind != 'column':
        if target.key_column not in table[0]: raise BlockError('请选择业务定位列', 422)
        col = table[0].index(target.key_column)
        keys = [r[col] for r in table[1:]]
        if not all(keys) or len(set(keys)) != len(keys) or target.key_value not in keys:
            raise BlockError('业务定位列必须非空且唯一，请重新选择', 422)
        row = keys.index(target.key_value) + 1
    if target.kind != 'row' and target.column not in table[0]: raise BlockError('目标列不存在', 422)
    return {'row': row, 'table_index': target.table_index, 'column': target.column}


def constrain_steps(source, target, plan):
    located = resolve_target(source, target)
    for step in plan.steps:
        if step.block_id != target.block_id: raise BlockError('候选超出所选对象所在章节', 422)
        if target.kind == 'mermaid':
            if step.action != 'mermaid_replace' or step.diagram_index != target.diagram_index:
                raise BlockError('候选超出所选 Mermaid 图，请缩小修改范围', 422)
            if target.object_id:
                token = r'(?<![\w-])' + re.escape(target.object_id) + r'(?![\w-])'
                if not re.search(token, step.diagram_source): raise BlockError('节点修改必须保留稳定对象 ID', 422)
                # ER entity blocks may change fields; everything outside that entity stays exact.
                entity = re.compile(r'(?m)^\s*' + re.escape(target.object_id) + r'\s*\{[^}]*\}')
                before, after = target.source.strip(), step.diagram_source.strip()
                if entity.search(before):
                    if not entity.search(after) or entity.sub('__SELECTED_ENTITY__', before) != entity.sub('__SELECTED_ENTITY__', after):
                        raise BlockError('候选修改了所选实体之外的图源；联动修改请改选整张图', 422)
                else:
                    a, b = before.splitlines(), after.splitlines()
                    for op, i, j, k, l in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
                        if op != 'equal' and any(line.strip() and not re.search(token, line) for line in a[i:j] + b[k:l]):
                            raise BlockError('候选修改了其他节点的图源；联动修改请改选整张图', 422)
        else:
            if step.action != 'table_set' or step.table_index != target.table_index or step.row < 1:
                raise BlockError('对象修改只允许更新所选范围的单元格；增删排序请移除对象后单独下令', 422)
            if target.kind != 'column' and step.row != located['row']:
                raise BlockError('候选修改了所选业务行之外的内容', 422)
            if target.kind != 'row' and step.column != target.column:
                raise BlockError('候选修改了所选列之外的内容', 422)
            # The row is resolved by an exact business key, never substring matching.
            step.row_match = ''
