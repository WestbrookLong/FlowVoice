from __future__ import annotations

import json


STYLE_LABELS = {
    "formal_paragraph": "正式段落",
    "meeting_notes": "会议纪要",
    "summary_bullets": "摘要要点",
    "todo_items": "待办事项",
    "faithful_cleanup": "忠实清理",
}

STYLE_INSTRUCTIONS = {
    "formal_paragraph": (
        "整理成书面、正式、连贯的中文段落；删除明显口头禅、卡顿和无意义重复；"
        "保留核心观点、语气强度和原有逻辑关系。"
    ),
    "meeting_notes": (
        "整理成会议纪要，包含主题、关键结论、讨论要点和后续行动；"
        "不要编造未出现的信息；缺失信息标注为未提及。"
    ),
    "summary_bullets": (
        "整理成清晰的要点列表，突出结论、理由、限制条件和下一步；"
        "不要加入原文没有的解释。"
    ),
    "todo_items": (
        "只提取明确或强暗示的待办事项；尽量包含负责人、动作、对象和截止时间；"
        "缺失的信息标注为未提及；不要把普通观点误判为待办。"
    ),
    "faithful_cleanup": (
        "只做忠实清理：修正口语冗余、断句、标点和明显错别字；"
        "保持原文顺序、视角和表达强度；不要总结、扩写或改写成正式文风。"
    ),
}


STYLE_OUTPUT_FORMATS = {
    "formal_paragraph": (
        "输出为一到数个自然段。不要使用列表，除非原文明显是列表结构。"
    ),
    "meeting_notes": (
        "输出格式：\n"
        "主题：...\n\n"
        "关键结论：\n"
        "- ...\n\n"
        "讨论要点：\n"
        "- ...\n\n"
        "后续行动：\n"
        "- 负责人：...；事项：...；截止时间：...\n"
        "如果某部分没有明确内容，写“未提及”。"
    ),
    "summary_bullets": (
        "输出为项目符号列表。每条只表达一个要点，优先保留结论、理由、限制和下一步。"
    ),
    "todo_items": (
        "只输出待办事项列表。格式：\n"
        "- [ ] 事项：...；负责人：未提及；截止时间：未提及\n"
        "如果没有明确待办，输出：未识别到明确待办事项。"
    ),
    "faithful_cleanup": (
        "输出清理后的原文。保持原文顺序和基本结构，不要总结，不要改写成会议纪要。"
    ),
}


def normalize_style(value: str | None) -> str:
    key = str(value or "").strip()
    return key if key in STYLE_INSTRUCTIONS else "meeting_notes"


def _system_prompt() -> str:
    return (
        "你是 FlowVoice 的中文语音文本整理 agent。输入来自实时语音转写，"
        "可能包含口头禅、重复、断句错误、轻微同音错字、停顿词和未完成句。"
        "你的任务是把它整理为可直接使用的中文文本。"
        "必须忠实于原文含义，不得添加原文没有的信息、观点、人物、时间、地点或因果关系。"
        "允许修正明显的语音转写错误、错别字、标点和断句；"
        "如果某处含义不确定，应保守处理，不要自行发挥。"
    )


def build_segment_prompt(
    previous_summaries: list[dict],
    previous_raw_text: str,
    new_raw_text: str,
    style: str,
) -> list[dict[str, str]]:
    normalized = normalize_style(style)
    summaries = json.dumps(previous_summaries, ensure_ascii=False, indent=2) if previous_summaries else "（暂无）"

    user = (
        "任务：分段整理实时草稿。\n"
        f"整理风格：{STYLE_LABELS[normalized]}\n"
        f"整理要求：{STYLE_INSTRUCTIONS[normalized]}\n\n"
        "重要边界：\n"
        "1. 此前上下文只用于理解代词、承接关系、术语和未完成句。\n"
        "2. 不要重新整理此前完整原文。\n"
        "3. 输出只能反映“本次新增原文”中的信息。\n"
        "4. 如果本次新增内容不完整，要保守表达，不要补全缺失结论。\n"
        "5. 不要编造未出现的人物、时间、地点、结论、因果或待办。\n\n"
        "此前分段整理结果：\n"
        f"{summaries}\n\n"
        "此前完整原文：\n"
        f"{previous_raw_text.strip() or '（暂无）'}\n\n"
        "本次新增原文：\n"
        f"{new_raw_text.strip()}\n\n"
        "请只输出合法 JSON object，不要使用 Markdown 代码块，不要输出解释文字。\n"
        "JSON 字段要求：\n"
        '- "title": string，8到20个中文字符，概括本次新增内容；不确定时用"未命名片段"。\n'
        '- "summary": string，1到3句话，只总结本次新增内容。\n'
        '- "keyPoints": string[]，0到6条，每条只包含一个要点。\n'
        '- "actionItems": object[]，没有明确待办时输出空数组 []。\n'
        '- "actionItems[].text": string，待办动作本身。\n'
        '- "actionItems[].owner": string|null，负责人未提及时为 null。\n'
        '- "actionItems[].deadline": string|null，截止时间未提及时为 null。\n'
        '- "confidence": "high"|"medium"|"low"，表示本段整理确定性。\n\n'
        "JSON 示例：\n"
        '{"title":"未命名片段","summary":"本段继续说明某个问题，但没有形成明确结论。",'
        '"keyPoints":[],"actionItems":[],"confidence":"low"}'
    )

    return [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": user},
    ]


def build_global_prompt(raw_text: str, segment_summaries: list[dict], style: str) -> list[dict[str, str]]:
    normalized = normalize_style(style)
    summaries = json.dumps(segment_summaries, ensure_ascii=False, indent=2) if segment_summaries else "（暂无）"

    user = (
        "任务：全局整理最终结果。\n"
        f"整理风格：{STYLE_LABELS[normalized]}\n"
        f"整理要求：{STYLE_INSTRUCTIONS[normalized]}\n"
        f"最终输出格式：{STYLE_OUTPUT_FORMATS[normalized]}\n\n"
        "重要边界：\n"
        "1. 必须以全量原始文本为最高依据，分段整理结果只作为辅助参考。\n"
        "2. 如果分段整理结果与全量原文冲突，以全量原文为准。\n"
        "3. 不要补充原文没有的信息、观点、人物、时间、地点、因果关系或待办。\n"
        "4. 可以修正明显语音转写错误、错别字、标点和断句。\n"
        "5. 如果信息缺失，根据所选格式标注“未提及”，不要猜测。\n\n"
        "此前全部分段整理结果：\n"
        f"{summaries}\n\n"
        "全量原始文本：\n"
        f"{raw_text.strip()}\n\n"
        "请输出最终可直接插入电脑光标处的文本。不要使用 Markdown 代码块。"
    )

    return [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": user},
    ]


def build_prompt(raw_text: str, style: str, *, incremental: bool) -> list[dict[str, str]]:
    if incremental:
        return build_segment_prompt([], "", raw_text, style)
    return build_global_prompt(raw_text, [], style)