"""Explicit workbench commands; ambiguous actions never write PRD text."""
import re
from app.services.block_service import BlockError


def ordinal(value):
    if value.isdigit(): return int(value)
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    if value == "十": return 10
    if "十" in value:
        a, b = value.split("十", 1)
        return digits.get(a, 1) * 10 + digits.get(b, 0)
    return digits.get(value, 0)


def dispatch(workbench, jobs, document_id, actor, instruction, target_ids, comment_id, request_id, expected_revision):
    text = instruction.strip()
    snapshot = workbench.blocks.snapshot(document_id, actor)
    if snapshot["document"]["revision"] != expected_revision: raise BlockError("文档已变化，请刷新后重试")
    if "会议包" in text and re.search(r"生成|整理|导出|比较", text):
        versions = list(dict.fromkeys(int(x) for x in re.findall(r"(?:v|版本\s*)\s*(\d+)", text, re.I)))
        if len(versions) != 2: raise BlockError("请给出两个版本，例如：比较 v3 和 v5，生成评审会议包", 422)
        result = workbench.meeting(document_id, actor, *versions)
        return {"type": "meeting", "summary": f"已生成 v{min(versions)} → v{max(versions)} 评审会议包，可预览并下载。", "meeting": result}
    data = workbench.inspect(document_id, actor)
    policy_match = re.match(r'^(?:确认|记录)业务决定[：:]\s*(.+)$', text, re.S)
    preference_match = re.match(r'^保存(本次|本文档|项目)编辑偏好[：:]\s*(.+)$', text, re.S)
    if policy_match or preference_match:
        from app.services.document_decisions import update_policy
        content = policy_match.group(1) if policy_match else preference_match.group(2)
        scope = 'document' if policy_match else {'本次': 'once', '本文档': 'document', '项目': 'project'}[preference_match.group(1)]
        if not content or len(content) > 6000: raise BlockError('请提供不超过 6000 字的明确决定或偏好', 422)
        update_policy(workbench, document_id, actor, data['revision'], {'kind': 'decision' if policy_match else 'preference',
            'scope': scope, 'status': 'active', 'text': content, 'reason': '用户在 Agent 对话中明确确认', 'block_ids': []})
        return {'type': 'policies', 'tab': 'policies', 'summary': '已保存' + ('当前文档的业务决定' if policy_match else '编辑偏好') + '。可在决定与偏好面板查看、替换或撤销；正文尚未修改。'}
    if re.search(r"收集|整理", text) and re.search(r"待确认|信息篮|信息收集篮", text):
        before = len(data["state"].get("questions", []))
        result = workbench.collect(document_id, actor, data["revision"])
        questions = result["state"].get("questions", [])
        summary = f"已收集待确认信息：新增 {len(questions)-before} 条，共 {len(questions)} 条。"
        names = {'open': '待答', 'answered': '已答待应用', 'applied': '已写入', 'ignored': '忽略', 'archived': '归档'}
        return {"type": "questions", "summary": summary + "\n" + "\n".join(f"{i+1}. [{names.get(q.get('status'), '待答')}] {q['text'][:150]}" for i, q in enumerate(questions)), "tab": "questions"}
    if "批注" in text and re.search(r"加入|放入|收进", text) and re.search(r"篮|待确认", text):
        candidates = [c for c in snapshot["comments"] if not c["resolved"] and
                      (c["id"] == comment_id if comment_id else any(t["block_id"] in target_ids for t in c["targets"]))]
        if len(candidates) != 1: raise BlockError("请先点开要收集的那条批注，再说：把这条批注加入资料缺失篮", 422)
        comment = candidates[0]
        payload = {"text": comment["text"], "block_ids": list(dict.fromkeys(t["block_id"] for t in comment["targets"])),
                   "source": "批注 " + comment["id"], "category": "information" if "资料" in text else "implementation" if "实现" in text else "business"}
        existing = next((q for q in data["state"].get("questions", []) if q["source"] == payload["source"]), None)
        if not existing: workbench.update(document_id, actor, data["revision"], "question", payload)
        return {"type": "questions", "summary": "该批注已在待确认信息篮中，可继续填写答案。", "tab": "questions"}
    answer = re.search(r"第([一二三四五六七八九十\d]+)(?:个|条)?问题(?:的)?(?:答案|答复)(?:是|为|[:：])\s*(.+)", text, re.S)
    if answer:
        number = ordinal(answer[1])
        questions = data["state"].get("questions", [])
        if number < 1 or number > len(questions): raise BlockError("该问题编号不存在，请先查看或收集待确认信息", 422)
        value = re.split(r"[，,。；;]\s*(?:请|先|并|然后|再|帮我)*\s*(?:给我|生成|预览|写入)", answer[2], maxsplit=1)[0].strip()
        if not value or len(value) > 6000: raise BlockError("请提供不超过 6000 字的明确答案", 422)
        question = questions[number-1]
        workbench.update(document_id, actor, data["revision"], "question", {**question, "answer": value})
        if re.search(r"预览|写入|应用|修改", text[answer.start(2):]):
            job = jobs.enqueue(document_id, actor, request_id, {"kind": "answers", "instruction": "",
                "target_ids": target_ids, "item_ids": [question["id"]]}, expected_revision)
            return {"type": "job", "summary": f"第 {number} 个问题的答案已保存；正在生成正文修改预览。", "job": job}
        return {"type": "questions", "summary": f"已保存第 {number} 个问题的答案，正文尚未改变。", "tab": "questions"}
    if re.search(r"(?:答案|回答).*(?:预览|写入)|(?:预览|写入).*已确认", text):
        selected = [q["id"] for q in data["state"].get("questions", []) if q.get("answer", "").strip()]
        if not selected: raise BlockError("请先填写并保存待确认问题的答案", 422)
        job = jobs.enqueue(document_id, actor, request_id, {"kind": "answers", "instruction": "", "target_ids": target_ids, "item_ids": selected}, expected_revision)
        return {"type": "job", "summary": "正在把已确认答案整理成正文修改预览。", "job": job}
    raise BlockError("请说明具体操作，例如：收集待确认信息、第三个问题的答案是……、比较 v3 和 v5 生成会议包", 422)
