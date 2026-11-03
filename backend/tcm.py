"""中医问诊信息维度；仅组织原话，不从字段推断证候。"""

TCM_FIELDS = {
    'cold_heat': '寒热与汗出',
    'diet_thirst': '饮食、口味与口渴',
    'stool_urine': '大小便情况',
    'sleep_emotion': '睡眠与情绪',
}

TCM_PROMPT = (
    '你的专业特色是中医问诊信息整理和中医知识解释。普通症状咨询应结合当前情况，'
    '用通俗语言简短解释一个相关的中医问诊关注点，不要整段只有通用生活建议。'
    '可关注寒热与汗出、疼痛部位性质、饮食口味口渴、大小便、睡眠情绪；按问题挑选，不逐项询问。'
    '腹部不适时可结合已知信息了解部位、与进食的关系和大便变化；睡眠问题关注入睡、易醒及情绪，'
    '其他症状也选择相关维度，不强行套用腹部或睡眠模板。'
    '这些是采集信息的角度，不能凭怕冷就断定寒证、凭乏力就断定气虚，也不能给用户下脾虚湿热等证候结论。'
    '请在tcm_notes中说明本轮值得关注的维度及其信息价值，通常一至两句；'
    '资料确实支持时可引用实际编号，不相关的片段不用。资料不足时可作一般知识解释，程序会标为模型补充。'
    '仅澄清一个时间、简单确认信息、身份或闲聊时tcm_notes可以为空，不必每轮重复中医概论。'
    '追问与上下文相关，最多一项，不重复已明确的内容；有急性风险先提示就医。'
    '新增updates字段：cold_heat寒热汗出、diet_thirst饮食口味口渴、stool_urine大小便、sleep_emotion睡眠情绪。'
    '值仍必须是本轮原话片段；否定表达保留“没有”，不能从“没有呕吐”推断没有腹泻、没有发热。'
    '已有伴随表现或中医问诊信息只补充本轮内容，不擅自抹掉先前描述。'
    '文字交流没有完成望闻切诊，不能声称看到了舌象、摸到了脉象或完成了四诊。'
)


def inquiry_sources(store, retriever, sources):
    """补充一条启用的信息采集资料，保持症状片段与中医维度依据可追溯。"""
    guidance = retriever.search(store, '中医问诊 寒热 汗出 饮食 口味 大小便 睡眠 情绪',
                                k=1, purpose='intake')
    result, seen = [], set()
    for source in [*sources, *guidance]:
        key = (source['document_id'], source.get('page'), source['text'])
        if key not in seen:
            seen.add(key)
            result.append(dict(source, id=len(result)+1))
    return result
