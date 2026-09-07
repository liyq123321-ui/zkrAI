from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "output" / "pdf" / "firstFlight-图工程运转逻辑.pdf"
PAGE_W, PAGE_H = landscape(A4)

FONT = "Deng"
FONT_BOLD = "DengBold"
pdfmetrics.registerFont(TTFont(FONT, r"C:\Windows\Fonts\Deng.ttf"))
pdfmetrics.registerFont(TTFont(FONT_BOLD, r"C:\Windows\Fonts\Dengb.ttf"))

NAVY = HexColor("#10243E")
BLUE = HexColor("#246BFD")
CYAN = HexColor("#31B7C2")
GREEN = HexColor("#25A56A")
AMBER = HexColor("#F1A737")
RED = HexColor("#DF5B5B")
PURPLE = HexColor("#7A67D8")
INK = HexColor("#182230")
MUTED = HexColor("#607086")
LINE = HexColor("#CCD6E2")
PAPER = HexColor("#F5F8FC")
PALE_BLUE = HexColor("#EAF1FF")
PALE_GREEN = HexColor("#E8F7F0")
PALE_AMBER = HexColor("#FFF4DE")
PALE_RED = HexColor("#FDEBEC")
PALE_PURPLE = HexColor("#F0EDFF")


def wrapped_lines(text, max_width, font=FONT, size=10):
    lines, current = [], ""
    for char in text:
        candidate = current + char
        if current and pdfmetrics.stringWidth(candidate, font, size) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def draw_text(c, text, x, y, max_width, size=10, color=INK, bold=False,
              leading=None, align="left"):
    font = FONT_BOLD if bold else FONT
    leading = leading or size * 1.35
    lines = []
    for paragraph in str(text).split("\n"):
        lines.extend(wrapped_lines(paragraph, max_width, font, size) or [""])
    c.setFont(font, size)
    c.setFillColor(color)
    for index, line in enumerate(lines):
        yy = y - index * leading
        if align == "center":
            c.drawCentredString(x + max_width / 2, yy, line)
        elif align == "right":
            c.drawRightString(x + max_width, yy, line)
        else:
            c.drawString(x, yy, line)
    return y - len(lines) * leading


def page_base(c, page_no, section):
    c.setFillColor(PAPER)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 34, PAGE_W, 34, fill=1, stroke=0)
    c.setFont(FONT_BOLD, 10)
    c.setFillColor(white)
    c.drawString(34, PAGE_H - 22, "firstFlight 图工程运转逻辑")
    c.setFont(FONT, 9)
    c.drawRightString(PAGE_W - 34, PAGE_H - 22, section)
    c.setStrokeColor(LINE)
    c.line(34, 25, PAGE_W - 34, 25)
    c.setFont(FONT, 8)
    c.setFillColor(MUTED)
    c.drawString(34, 12, "基于当前仓库源码整理 | 2026-09-07")
    c.drawRightString(PAGE_W - 34, 12, f"{page_no} / 6")


def title(c, heading, subtitle=None):
    c.setFillColor(INK)
    c.setFont(FONT_BOLD, 24)
    c.drawString(42, PAGE_H - 76, heading)
    if subtitle:
        draw_text(c, subtitle, 43, PAGE_H - 97, PAGE_W - 86, 10, MUTED)


def box(c, x, y, w, h, heading, body="", fill=white, stroke=LINE,
        accent=None, heading_size=12, body_size=9, radius=10):
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(1)
    c.roundRect(x, y, w, h, radius, fill=1, stroke=1)
    if accent:
        c.setFillColor(accent)
        c.roundRect(x, y, 6, h, 3, fill=1, stroke=0)
    tx = x + 14
    tw = w - 28
    draw_text(c, heading, tx, y + h - 21, tw, heading_size, INK, True)
    if body:
        draw_text(c, body, tx, y + h - 42, tw, body_size, MUTED, False,
                  leading=body_size * 1.45)


def arrow(c, x1, y1, x2, y2, color=BLUE, width=2, label=None, curve=0):
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(width)
    if curve:
        midx = (x1 + x2) / 2
        c.bezier(x1, y1, midx, y1 + curve, midx, y2 + curve, x2, y2)
    else:
        c.line(x1, y1, x2, y2)
    import math
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 7
    left = (x2 - size * math.cos(angle - 0.5), y2 - size * math.sin(angle - 0.5))
    right = (x2 - size * math.cos(angle + 0.5), y2 - size * math.sin(angle + 0.5))
    path = c.beginPath()
    path.moveTo(x2, y2)
    path.lineTo(*left)
    path.lineTo(*right)
    path.close()
    c.drawPath(path, fill=1, stroke=0)
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        c.setFillColor(PAPER)
        c.roundRect(mx - 31, my - 8, 62, 16, 5, fill=1, stroke=0)
        c.setFillColor(MUTED)
        c.setFont(FONT, 8)
        c.drawCentredString(mx, my - 3, label)


def pill(c, x, y, text, fill, color=INK, width=None):
    width = width or max(52, pdfmetrics.stringWidth(text, FONT_BOLD, 9) + 20)
    c.setFillColor(fill)
    c.roundRect(x, y, width, 21, 10, fill=1, stroke=0)
    c.setFillColor(color)
    c.setFont(FONT_BOLD, 9)
    c.drawCentredString(x + width / 2, y + 6, text)
    return width


def node(c, cx, cy, label, subtitle, color=BLUE, r=29):
    c.setFillColor(color)
    c.circle(cx, cy, r, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont(FONT_BOLD, 15)
    c.drawCentredString(cx, cy - 5, label)
    c.setFillColor(INK)
    c.setFont(FONT_BOLD, 9)
    c.drawCentredString(cx, cy - r - 18, subtitle)


def page_one(c):
    page_base(c, 1, "总览")
    title(c, "一套自研的三层图工程", "不是通用图引擎框架，而是由确定性策略、DAG 算法和持久化账本共同组成。")
    levels = [
        ("01", "项目状态图 FSM", "控制业务阶段和合法动作\n回答“现在能做什么”", PALE_BLUE, BLUE),
        ("02", "任务规划 DAG", "按依赖分层生成任务计划\n回答“工作应如何拆解”", PALE_PURPLE, PURPLE),
        ("03", "任务执行 DAG", "前置完成后解锁后继任务\n回答“哪个任务现在可启动”", PALE_GREEN, GREEN),
    ]
    y = 362
    for index, (num, heading, body, fill, color) in enumerate(levels):
        x = 84 + index * 245
        box(c, x, y, 205, 115, heading, body, fill, color, color)
        pill(c, x + 153, y + 82, num, color, white, 38)
        if index < 2:
            arrow(c, x + 205, y + 58, x + 238, y + 58, NAVY, 2)
    box(c, 84, 185, 695, 120, "共同运行底座",
        "状态真源：后端投影 legal_actions / next_action\n"
        "可靠性：command_id 幂等 + expected_state_version 乐观锁\n"
        "一致性：prepare / materialize 两阶段命令 + 单事务落库\n"
        "审计性：AgentCall、CommandAttempt、ProcessedCommand、AuditEvent、WorkItemRun",
        white, LINE, NAVY, 13, 10)
    c.setFillColor(NAVY)
    c.roundRect(84, 67, 695, 76, 12, fill=1, stroke=0)
    draw_text(c, "核心规律", 104, 121, 110, 12, white, True)
    draw_text(c, "FSM 决定阶段，DAG 决定顺序，账本决定状态，两阶段命令保证每次推进可重试、可追踪、不会重复落库。",
              205, 119, 548, 11, white, False, leading=16)


def page_two(c):
    page_base(c, 2, "项目状态图")
    title(c, "项目生命周期状态机", "ProjectPhase 与 SpecStatus 组成复合状态；前端只显示后端允许的动作。")
    y_top = 392
    stages = [
        (75, "INTAKE", "需求摄入", PALE_BLUE, BLUE),
        (240, "NEED_CLARIFICATION", "澄清门", PALE_AMBER, AMBER),
        (455, "SPECIFICATION", "生成 PRD", PALE_PURPLE, PURPLE),
        (650, "REVIEW", "规则 + Agent + 人工", PALE_GREEN, GREEN),
    ]
    for x, state, desc, fill, color in stages:
        w = 150 if state != "NEED_CLARIFICATION" else 180
        box(c, x, y_top, w, 76, state, desc, fill, color, color, 11, 9)
    arrow(c, 225, y_top + 38, 240, y_top + 38, BLUE)
    arrow(c, 420, y_top + 38, 455, y_top + 38, AMBER)
    arrow(c, 605, y_top + 38, 650, y_top + 38, PURPLE)
    arrow(c, 150, y_top, 455, y_top, BLUE, 2, "无需澄清", curve=-55)

    statuses = [
        (92, 240, "AUTO_REVIEW", "自动审核中", PALE_BLUE, BLUE),
        (252, 240, "HUMAN_REVIEW", "等待人工决定", PALE_GREEN, GREEN),
        (422, 240, "REWORK", "修改后再审核", PALE_AMBER, AMBER),
        (582, 240, "NEED_INFO", "补充信息", PALE_RED, RED),
    ]
    c.setFont(FONT_BOLD, 11)
    c.setFillColor(INK)
    c.drawString(76, 345, "REVIEW 内部子状态")
    for x, y, state, desc, fill, color in statuses:
        box(c, x, y, 145, 68, state, desc, fill, color, color, 10, 8)
    arrow(c, 237, 274, 252, 274, BLUE)
    arrow(c, 397, 274, 422, 274, GREEN, label="返工")
    arrow(c, 567, 274, 582, 274, AMBER, label="需信息")
    arrow(c, 494, 240, 324, 240, AMBER, 2, "新版 PRD", curve=-32)

    box(c, 138, 72, 225, 83, "APPROVED", "人工批准当前 PRD\n允许转换为任务图", PALE_GREEN, GREEN, GREEN)
    box(c, 500, 72, 225, 83, "AGENT_SPECS_READY", "任务与 Agent Spec 已就绪\n进入依赖图执行阶段", PALE_BLUE, BLUE, BLUE)
    arrow(c, 325, 240, 250, 155, GREEN, 2, "批准")
    arrow(c, 363, 113, 500, 113, NAVY, 2, "convert_to_work_item")


def page_three(c):
    page_base(c, 3, "分阶段拆解")
    title(c, "从批准的 PRD 编译出任务 DAG", "先生成结构，再按拓扑层生成详细计划，最后统一审核和原子发布。")
    steps = [
        (50, "1", "批准 PRD 快照", "绑定项目 UUID、Spec UUID、内容哈希和输入引用", PALE_BLUE, BLUE),
        (205, "2", "基础拆解", "生成 Milestone、Task、依赖边与 Agent Spec 骨架", PALE_PURPLE, PURPLE),
        (360, "3", "结构校验", "父子关系、唯一键、依赖目标、无环、FR/NFR 覆盖", PALE_AMBER, AMBER),
        (515, "4", "分层规划", "Kahn 拓扑 Wave；层间串行、层内最多并发 2 个", PALE_GREEN, GREEN),
        (670, "5", "评审与发布", "完整契约审核；全部通过后一次事务写入图", PALE_BLUE, BLUE),
    ]
    for index, (x, num, heading, body, fill, color) in enumerate(steps):
        box(c, x, 343, 132, 132, heading, body, fill, color, color, 11, 8)
        pill(c, x + 92, 353, num, color, white, 28)
        if index < len(steps) - 1:
            arrow(c, x + 132, 409, x + 150, 409, NAVY)

    c.setFont(FONT_BOLD, 12)
    c.setFillColor(INK)
    c.drawString(52, 300, "拓扑 Wave 示例")
    columns = [
        (95, "WAVE 0", [("T1", "领域模型"), ("T2", "接口契约")], BLUE),
        (330, "WAVE 1", [("T3", "业务服务"), ("T4", "前端视图")], PURPLE),
        (565, "WAVE 2", [("T5", "集成验收")], GREEN),
    ]
    for x, label, tasks, color in columns:
        pill(c, x, 255, label, color, white, 115)
        yy = 192
        for task_id, task_name in tasks:
            box(c, x, yy, 150, 48, f"{task_id}  {task_name}", "可并行规划", white, color, color, 10, 8, 7)
            yy -= 61
    arrow(c, 245, 190, 330, 190, NAVY, 2, "依赖完成")
    arrow(c, 480, 190, 565, 190, NAVY, 2, "依赖完成")
    box(c, 52, 61, 740, 49, "失败恢复规律",
        "某个任务计划失败时，已成功的同级计划保留为 AgentCall 检查点；重试仅补齐缺失或失效的任务，不公开半成品 WorkItem。",
        PALE_RED, RED, RED, 10, 8)


def page_four(c):
    page_base(c, 4, "任务执行 DAG")
    title(c, "依赖完成后逐步解锁任务", "数据存“当前任务依赖谁”，显示时转换成“前置任务指向后继任务”。")
    coords = {
        "A": (125, 355), "B": (125, 210), "D": (330, 190),
        "C": (420, 285), "E": (675, 260),
    }
    arrows = [("A", "C"), ("B", "C"), ("C", "E"), ("D", "E")]
    for source, target in arrows:
        sx, sy = coords[source]
        tx, ty = coords[target]
        arrow(c, sx + 31, sy, tx - 31, ty, NAVY, 2)
    labels = {
        "A": "领域模型", "B": "接口契约", "C": "业务服务",
        "D": "测试基线", "E": "集成验收",
    }
    for key, (x, y) in coords.items():
        node(c, x, y, key, labels[key], BLUE if key in {"A", "B", "D"} else PURPLE if key == "C" else GREEN)

    box(c, 52, 55, 235, 78, "初始可启动集合", "A、B、D 无未完成依赖；C 等待 A+B；E 等待 C+D", PALE_BLUE, BLUE, BLUE)
    box(c, 304, 55, 235, 78, "完成 A 和 B 后", "C 获得 start_task；状态来自最新 WorkItemRun", PALE_PURPLE, PURPLE, PURPLE)
    box(c, 556, 55, 235, 78, "完成 C 和 D 后", "E 被解锁，整条依赖链可以收敛", PALE_GREEN, GREEN, GREEN)
    pill(c, 52, 455, "启动条件", NAVY, white, 78)
    draw_text(c, "TASK + executable=true + status=todo + 所有前置任务均为 done",
              143, 469, 618, 11, INK, True)


def page_five(c):
    page_base(c, 5, "执行状态账本")
    title(c, "状态由追加式账本推导，而不是覆盖节点", "每个操作新增一条 WorkItemRun；最新记录代表当前状态，历史完整保留。")
    states = [
        (68, "无记录", "todo", PALE_BLUE, BLUE),
        (245, "STARTED", "in_progress", PALE_PURPLE, PURPLE),
        (465, "SUCCEEDED", "done", PALE_GREEN, GREEN),
    ]
    for idx, (x, ledger, view, fill, color) in enumerate(states):
        box(c, x, 355, 155, 92, ledger, f"客户端状态：{view}", fill, color, color)
        if idx < len(states) - 1:
            arrow(c, x + 155, 401, x + 177, 401, NAVY)
    box(c, 245, 225, 155, 78, "FAILED", "客户端状态：failed", PALE_RED, RED, RED)
    arrow(c, 322, 355, 322, 303, RED, 2, "失败")

    c.setFont(FONT_BOLD, 12)
    c.setFillColor(INK)
    c.drawString(68, 198, "一次任务命令的判定链")
    chain = [
        (68, "项目状态合法"), (225, "节点可执行"), (382, "依赖已完成"),
        (539, "状态允许操作"), (696, "追加账本"),
    ]
    for idx, (x, text) in enumerate(chain):
        pill(c, x, 161, text, PALE_BLUE if idx < 4 else PALE_GREEN,
             NAVY if idx < 4 else GREEN, 120)
        if idx < len(chain) - 1:
            arrow(c, x + 120, 171, x + 148, 171, LINE, 1.5)

    box(c, 68, 45, 338, 88, "当前边界",
        "任务执行目前是依赖感知的人工推进：系统提供开始、完成、失败按钮，但不会自动拉起子 Agent、执行代码或自动完成任务。",
        white, LINE, NAVY, 12, 9)
    box(c, 431, 45, 338, 88, "当前规则漂移",
        "投影层会给 failed / blocked 显示 start_task，但执行服务只允许 todo 启动。失败任务的“重新开始”目前会被后端拒绝。",
        PALE_RED, RED, RED, 12, 9)


def page_six(c):
    page_base(c, 6, "两阶段命令")
    title(c, "让整套图可靠运转的命令引擎", "外部工作与数据库提交分离，使每次推进都具备幂等、并发保护和审计证据。")
    y = 350
    phases = [
        (54, "请求", "command_id\nexpected_state_version", PALE_BLUE, BLUE),
        (205, "PREPARING", "调用 Agent\n生成并验证结果", PALE_PURPLE, PURPLE),
        (378, "PREPARED", "结果与 AgentCall\n作为检查点持久化", PALE_AMBER, AMBER),
        (551, "MATERIALIZE", "事务内重新校验\n写节点、边、状态", PALE_GREEN, GREEN),
        (708, "RECEIPT", "ProcessedCommand\nAuditEvent", PALE_BLUE, BLUE),
    ]
    widths = [125, 145, 145, 145, 120]
    for idx, ((x, heading, body, fill, color), w) in enumerate(zip(phases, widths)):
        box(c, x, y, w, 105, heading, body, fill, color, color, 11, 8)
        if idx < len(phases) - 1:
            next_x = phases[idx + 1][0]
            arrow(c, x + w, y + 52, next_x, y + 52, NAVY, 2)

    safeguards = [
        (65, 212, "幂等", "相同 command_id + 相同输入直接返回已保存结果", BLUE),
        (270, 212, "乐观锁", "state_version 已变化时拒绝旧页面请求", PURPLE),
        (475, 212, "原子性", "图节点、边、状态和审计在一个事务中提交", GREEN),
        (680, 212, "恢复", "超时后复用 PREPARED 与 AgentCall 证据", AMBER),
    ]
    for x, y0, heading, body, color in safeguards:
        c.setFillColor(color)
        c.circle(x + 50, y0 + 48, 34, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont(FONT_BOLD, 11)
        c.drawCentredString(x + 50, y0 + 44, heading)
        draw_text(c, body, x, y0 - 2, 100, 8, MUTED, align="center", leading=12)

    c.setFillColor(NAVY)
    c.roundRect(62, 67, 718, 75, 12, fill=1, stroke=0)
    draw_text(c, "最终运转公式", 82, 119, 120, 12, white, True)
    draw_text(c, "FSM 控制阶段  +  DAG 控制顺序  +  WorkItemRun 控制状态  +  两阶段命令控制一致性",
              202, 118, 550, 12, white, True, align="center")


def build():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(OUTPUT), pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    c.setTitle("firstFlight 图工程运转逻辑")
    c.setAuthor("Codex")
    for fn in (page_one, page_two, page_three, page_four, page_five, page_six):
        fn(c)
        c.showPage()
    c.save()
    print(OUTPUT)


if __name__ == "__main__":
    build()
