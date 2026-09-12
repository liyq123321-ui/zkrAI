"""Outline titles transcribed from the supplied PRD templates, without example business."""
import re
from app.ai.provider import BlockPlan, PlanNode

TEMPLATES = {
    "alibaba": {"label": "阿里 PRD 模板", "source": "阿里产品经理_PRD需求文档.pdf（7 页）", "outline": """# 文档说明
## 更新记录
## 变更日志
## 名词术语表
## 产品说明
### 产品背景
### 用户问题
### 产品目标
### 用户场景
## 开发排期
# 产品概念设计
## 产品概念
## 功能结构
## 信息结构图
## 产品结构图
## Feature List
## 页面流程图
# 全局说明
## 功能权限
## 全局交互
## 常用字段说明
# 需求说明
## 需求列表
## 需求明细
## 非功能需求
# 项目规划
# 成功指标"""},
    "tencent": {"label": "鹅厂 PRD 模板", "source": "鹅厂PRD需求文档（含模板）.pdf（模板部分，排除写作教程）", "outline": """# 变更记录
# 产品概述
## 业务背景
## 业务场景
## 产品定位
## 服务对象
## 产品逻辑
## 信息架构
## 角色术语
# 需求说明
## 需求列表
## 需求明细
## 非功能需求
# 人员与排期
# 成功指标"""},
}


def parse_outline(text: str) -> BlockPlan:
    rows, fence = [], None
    for line in text.splitlines():
        stripped = line.strip()
        marker = re.match(r'^(`{3,}|~{3,})', stripped)
        if marker:
            token = marker.group(1)[0]
            fence = None if fence == token else token if fence is None else fence
            continue
        if fence or not stripped:
            continue
        heading = re.match(r'^(#{1,6})\s+(.+?)\s*#*$', stripped)
        if heading:
            rows.append((len(heading.group(1)), heading.group(2)))
    if not rows:
        rows = [(1 + (len(line) - len(line.lstrip())) // 2,
                 re.sub(r'^[-*+]\s+|^\d+[.、]\s*', '', line.strip()))
                for line in text.splitlines() if line.strip()]
    if not rows or len(rows) > 100:
        raise ValueError("大纲需包含 1–100 个标题")
    stack, nodes = [], []
    for index, (depth, title) in enumerate(rows):
        while stack and stack[-1][0] >= depth:
            stack.pop()
        key = f"section_{index + 1}"
        nodes.append(PlanNode(key=key, title=title[:200], parent_key=stack[-1][1] if stack else None,
                              instruction="只描述本章节；未确认的信息明确标注待确认。", dependencies=[]))
        stack.append((depth, key))
    return BlockPlan(blocks=nodes)


def validate_plan(plan: BlockPlan):
    nodes = {node.key: node for node in plan.blocks}
    if len(nodes) != len(plan.blocks):
        raise ValueError("Block key 重复")
    for edge in (lambda n: [n.parent_key] if n.parent_key else [], lambda n: n.dependencies):
        visited, active = set(), set()
        def visit(key):
            if key not in nodes:
                raise ValueError("大纲包含不存在的引用")
            if key in active:
                raise ValueError("层级或依赖存在循环")
            if key in visited:
                return
            active.add(key)
            for upstream in edge(nodes[key]):
                visit(upstream)
            active.remove(key)
            visited.add(key)
        for key in nodes:
            visit(key)
