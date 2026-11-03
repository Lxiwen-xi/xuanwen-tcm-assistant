"""症状检索上下文：保留来源页码，只给建议生成器提供知识解释片段。"""
import hashlib
import json
import re
from .advice import positive_match

RAG_VERSION = 'symptom-rag-v1'
EXPANSIONS = [
    (r'失眠|睡不着|入睡困难|易醒|早醒|睡不好', '不寐 失眠 睡眠'),
    (r'反酸|烧心|胃胀|腹胀|嗳气', '反酸 胃胀 痞满'),
    (r'乏力|疲劳|疲倦|没精神|没力气', '乏力 疲劳'),
    (r'咳嗽|干咳|咳痰', '咳嗽'),
    (r'头痛|头疼', '头痛'),
    (r'耳鸣', '耳鸣'), (r'便秘|大便干', '便秘'),
    (r'腹泻|拉肚子|便溏', '泄泻 腹泻'), (r'口干', '口干'),
    (r'腹痛|肚子疼|肚子痛|肚子不舒服', '腹痛 腹部不适'),
]
UNSAFE = re.compile(r'参考方|代表方|治法|方剂|中药|用药|煎|炮制|服用|停药|配伍|药方|'
                    r'针灸|艾灸|拔罐|刮痧|熏蒸|选药|分经而治|[一-龥]{2,8}(?:汤|丸|散|煎)(?:加减|合|类|[，、；。]|$)|'
                    r'\d+(?:\.\d+)?\s*(?:mg|毫克|克)', re.I)


def educational_excerpt(text):
    """保留定义、病机概述和问诊线索；略过证型-治法-方药表格。

    不是通用PDF表格解析器：遇到续表宁可略过，不能把拆开的列配成个人建议。
    """
    paged = bool(re.search(r'【第\d+页】',text))
    text = re.sub(r'【第\d+页】', '', text).strip()
    if re.search(r'药名\s*性味归经|方名\s*核心组成|第三篇\s*中药|第四篇\s*方剂', text):
        return ''
    table = paged  # 续页先保守跳过，直到找到章节或明确的解释段。
    buffer, result = [], []

    def flush():
        if not buffer:
            return
        joined = ''.join(buffer).strip()
        buffer.clear()
        for sentence in re.split(r'(?<=[。；])', joined):
            if sentence.strip() and not UNSAFE.search(sentence) and not re.search(
                r'Agent|系统提示|应答|话术|检索优先|必须输出|阶段\d', sentence, re.I):
                result.append(sentence.strip())

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        heading = bool(re.match(r'(?:第\d+章|\d+\.\d+\s)', line))
        if heading:
            flush()
            table = False
            if not UNSAFE.search(line):
                result.append('\n'+line)
            continue
        if line in {'证型','辨证要点','治法','参考方','脉象','指感特点','药名','方名','功效','主治要点',
                    '六经','纲领','脏腑','腑','五行','术语','主症','病机','治法要点','通俗解释'}:
            flush()
            table = True
            continue
        if re.match(r'病机[：:]|病因病机[：:]|就医提示[：:]|八纲即|中医诊|问诊是', line):
            flush()
            table = False
        if not table:
            buffer.append(line)
    flush()
    # 只剩标题的续表不作为知识依据。
    content = '\n'.join(result).strip()
    if len(re.sub(r'第\d+章[^\n]*|\d+\.\d+\s*', '', content).strip()) < 25:
        return ''
    return content[:1200]


def symptom_query(profile):
    text = profile.get('complaint','') + '。' + profile.get('associated','')
    terms = [value for pattern,value in EXPANSIONS if positive_match(pattern,text)]
    if terms:
        if positive_match(r'孩子|宝宝|小孩|婴儿|儿童|幼儿',profile.get('complaint','')):
            terms.append('小儿')
        return ' '.join(dict.fromkeys(' '.join(terms).split()))
    # 不包含用药史和年龄等，减少与主题无关的匹配。
    parts = [part for part in re.split(r'[，。；]|但是|但', text)
             if part.strip() and not re.match(r'\s*(?:也?没有|无|否认|未明确)',part)]
    return ' '.join(parts)[:160]


def refresh_context(store, retriever, session):
    if not session.get('profile',{}).get('complaint') or session.get('stage') == 'attention':
        session.pop('_rag_context', None)
        return
    query = symptom_query(session['profile'])
    docs = store.documents(active_only=True)
    stamp = [(d['id'],d['title'],d['version'],d['url'],d['content']) for d in sorted(docs,key=lambda d:d['id'])]
    revision = hashlib.sha256((RAG_VERSION+json.dumps(stamp,ensure_ascii=False)).encode()).hexdigest()[:20]
    old = session.get('_rag_context') or {}
    if old.get('revision') == revision and old.get('query') == query:
        return
    hits = retriever.search(store, query, k=3, purpose='symptom') if query else []
    session['_rag_context'] = dict(revision=revision, query=query, sources=hits,
        status='matched' if hits else 'no_match', external_hits=sum(not h['builtin'] for h in hits))
    session.pop('_model_advice', None)
