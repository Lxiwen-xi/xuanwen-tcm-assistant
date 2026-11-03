"""单一 LangGraph 工作流：整理 → 分流 → 追问 / 检索 / 风险提示。"""
import re
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from .llm import format_evidence, converse, ModelError
from .advice import build_advice, wants_advice, advice_signature
from .rag import refresh_context, symptom_query
from .intent import analyze_turn, knowledge_question
from .tcm import TCM_FIELDS, inquiry_sources

FIELDS = {
    'complaint': '主要不适', 'duration': '持续时间', 'frequency': '发生频率',
    'associated': '伴随情况', 'history': '既往病史', 'medicines': '当前用药及过敏情况',
    'details': '补充信息',
    **TCM_FIELDS,
}
QUESTIONS = {
    'duration': '这种情况大约持续多久了？例如“3天”或“两个星期”，不清楚也可以说明。',
    'frequency': '大约多久出现一次，最近有没有变化？例如“每晚出现”或“偶尔发生”。',
    'associated': '还有哪些伴随不适？如果没有，可以直接说“没有其他不适”。',
    'history': '有没有需要一并说明的既往病史？也可以选择跳过。',
    'medicines': '目前有无用药或已知药物过敏？不愿提供可以跳过。',
}


def risk_flags(text):
    """演示规则，非临床分诊。逐个短句处理否定、历史与知识性提问。"""
    flags = []
    rules = [('呼吸困难', r'呼吸困难|喘不上气|无法呼吸'),
             ('意识异常', r'昏迷|意识不清|叫不醒'),
             ('胸部急性不适', r'胸痛|胸口剧痛'),
             ('明显出血', r'大量出血|呕血|吐血'),
             ('突发剧烈头痛', r'(?:突然|突发)[^，。]{0,5}(?:剧烈头痛|剧烈头疼)'),
             ('咳血或黑便', r'咳血|咯血|黑便|柏油样便')]
    for clause in re.split(r'[，。！？；\n]|但是|不过|但', text):
        for label, pattern in rules:
            for m in re.finditer(pattern, clause):
                before, after = clause[:m.start()], clause[m.end():]
                if re.search(r'(没有|并无|无|否认|不伴|未出现|不是)[^，。]{0,4}$', before):
                    continue
                if re.search(r'什么是|是什么意思|科普|如何识别|如果|假如', clause):
                    continue
                historical = bool(re.search(r'去年|以前|曾经|上个月|已缓解|已经好了', clause))
                current = bool(re.search(r'现在|目前|此刻|又出现|仍然', clause))
                if historical and not current:
                    continue
                if re.match(r'(已消失|已经消失|已缓解)', after):
                    continue
                flags.append(label)
    return sorted(set(flags))


def summary_text(session):
    lines = ['# 问诊信息摘要', '', '本摘要为用户描述的整理，未经医生确认，不是诊断报告。', '']
    for key, label in FIELDS.items():
        if key in TCM_FIELDS: continue
        lines.append(f'- {label}：{session["profile"].get(key) or "未提供"}')
    lines += ['', '## 中医问诊信息（用户自述）']
    lines += [f'- {label}：{session["profile"].get(key) or "未提供"}' for key,label in TCM_FIELDS.items()]
    lines += ['', '## 本次提示', session.get('risk_notice') or '未触发演示风险规则；这不代表已排除风险。']
    advice = build_advice(session)
    if advice:
        lines += ['', '## 症状解读与建议', advice['text']]
    sources = advice['sources'] if advice else session.get('last_sources', [])
    lines += ['', '## 本摘要使用的参考资料']
    lines += [f'- [{s["id"]}] {s["title"]}'+(f' · 第{s["page"]}页' if s.get('page') else '')+
              f'（{s["version"]}） {s["url"]}' for s in sources] or ['- 尚无参考资料']
    return '\n'.join(lines)


class FlowState(TypedDict, total=False):
    session: dict
    text: str
    route: str
    reply: str
    sources: list
    mode: str
    first_description: bool
    updated_symptom: bool


def build_workflow(store, retriever):
    def recent(s):
        return [dict(role=m['role'], content=m['content'][:2000]) for m in s['messages'][-12:]]

    def report(s):
        refresh_context(store, retriever, s)
        baseline = build_advice({**s, '_model_advice': None})
        result = dict(**baseline, mode='offline_advice')
        return result

    def collect(state):
        s, text = state['session'], state['text'].strip()
        s['_turn_plan'] = None
        s['_model_failed_turn'] = False
        s['model_error'] = ''
        s['risk_current'] = risk_flags(text)
        if s['risk_current']:
            s['risk_notice'] = '本次描述触发提示：' + '、'.join(s['risk_current'])
            s['stage'] = 'attention'
            return dict(session=s, route='risk')
        # 已触发的提醒在本会话保持，避免用户换个问题后被自动清除。
        if s.get('stage') == 'attention':
            return dict(session=s, route='risk')
        if re.search(r'开.*方|开药|(?:吃|用|服用)(?:什么|哪种|哪些|哪款).{0,3}药|'
                     r'(?:推荐|建议|给我).{0,8}(?:中药|中成药|药物)|药方|剂量|几克|多少克|确诊', text) or (
            '辨证' in text and not knowledge_question(text)):
            return dict(session=s, route='boundary')
        if s.get('model_consent'):
            return dict(session=s,route='online_chat')
        if s['profile'].get('complaint') and wants_advice(text):
            return dict(session=s, route='advice')
        decision = analyze_turn(text,s['profile'],s.get('last_slot'))
        if decision['kind']=='knowledge':
            return dict(session=s, route='retrieve')
        if decision['kind']=='resume':
            if s['profile'].get('complaint'):
                s['_paused']=False
                return dict(session=s,route='interview')
            decision = dict(kind='chat',reply='还没有记录身体不适。你可以描述主要不适，也可以直接问知识问题。')
        if decision['kind']!='intake':
            if decision.get('pause'): s['_paused']=True
            reply = decision.get('reply') or ('我可以帮你查询中医与一般健康知识，或整理身体不适。你想了解哪方面？' if decision['kind']=='chat' else
                '我还不能确定这句话是在描述身体不适、回答上一项，还是想查询知识。请再说明一下；本轮没有修改问诊信息。')
            return dict(session=s,route='chat',reply=reply,sources=[],mode='conversation' if decision['kind']=='chat' else 'clarification')
        p = s['profile']
        first_description = not p.get('complaint')
        slot = s.get('last_slot')
        for key,value in decision.get('updates',{}).items():
            if key=='complaint' and p.get(key):
                if value not in p[key]: p[key]=(p[key]+'；补充：'+value)[:1200]
            else:
                p[key]='未明确（用户跳过）' if value in {'跳过','不想说'} else value
        if not p.get('complaint'):
            return dict(session=s,route='chat',reply='已记录你明确提供的信息。若要了解身体不适，请再描述主要表现；也可以直接问知识问题。',sources=[],mode='conversation')
        s['_paused']=False
        s['last_slot'] = slot if slot and not p.get(slot) else None
        return dict(session=s, route='interview', first_description=first_description,
            updated_symptom='complaint' in decision.get('updates',{}) or 'associated' in decision.get('updates',{}))

    def interview(state):
        s = state['session']
        pending = s.get('last_slot')
        keys = ([pending] if pending in QUESTIONS and not s['profile'].get(pending) else []) + list(QUESTIONS)
        for key in keys:
            if not s['profile'].get(key):
                if key not in s['asked']: s['asked'].append(key)
                s['last_slot'] = key
                s['stage'] = 'collecting'
                plan = s.get('_turn_plan') or {}
                dynamic = bool(plan.get('ok') and plan.get('field') == key and plan.get('question'))
                question = plan['question'] if dynamic else QUESTIONS[key]
                if state.get('first_description') or state.get('updated_symptom'):
                    answer = report(s)
                    return dict(session=s, reply=answer['text']+'\n\n接下来确认一项：\n'+question,
                        sources=answer['sources'], mode=answer['mode'])
                mode = 'online_question' if dynamic else ('guided_fallback' if s.get('_model_failed_turn') else 'guided')
                return dict(session=s, reply=question, sources=[], mode=mode)
        s['stage'] = 'summarized'
        answer = report(s)
        return dict(session=s, reply=answer['text']+'\n\n你可以继续补充或修改右侧信息，再查看更新后的建议。',
            sources=answer['sources'], mode=answer['mode'])

    def advice(state):
        answer = report(state['session'])
        return dict(reply=answer['text'], sources=answer['sources'], mode=answer['mode'])

    def online_chat(state):
        s,text=state['session'],state['text']
        p=s['profile']
        raw_query=text+' '+p.get('complaint','')+' '+p.get('associated','')+' '+' '.join(p.get(k,'') for k in TCM_FIELDS)
        query=(raw_query+' '+symptom_query({'complaint':raw_query}))[:700]
        sources=retriever.search(store,query,k=3,purpose='symptom')
        sources=inquiry_sources(store,retriever,sources)
        try:
            result=converse(text,p,recent(s),s.get('_pending_question') or QUESTIONS.get(s.get('last_slot'),''),sources)
        except ModelError as exc:
            s['model_error']=str(exc)
            return dict(session=s,reply='本轮未能取得可用的 DeepSeek 回答：'+str(exc)+
                '\n你的消息已保留，已有问诊信息没有被改动。可以稍后重试；取消模型授权后也可使用离线整理。',
                sources=[],mode='chat_api_fallback')
        for key,value in result['updates'].items():
            if key in {'complaint','details','associated'} | set(TCM_FIELDS) and p.get(key):
                if value not in p[key]:
                    limit=1500 if key=='details' else 1200 if key=='complaint' else 500
                    p[key]=(p[key]+'；补充：'+value)[:limit]
            else: p[key]=value
        if result['intent'] in {'consultation','clarification'}:
            s['last_slot']=result['question_field'] or None
            s['_pending_question']=result['follow_up']
            if p.get('complaint'): s['stage']='collecting' if result['follow_up'] else 'summarized'
        refresh_context(store,retriever,s)
        mode='online_chat_supplement' if result['supplement'] else 'online_chat'
        retrieval=dict(status='matched' if sources else 'no_match',hits=len(sources),
            external_hits=sum(not x.get('builtin') for x in sources),query=query)
        if p.get('complaint') and result['intent'] in {'consultation','clarification'}:
            baseline=build_advice({**s,'_model_advice':None})
            report=dict(text=result['text'],sources=result['sources'],topics=baseline['topics'],
                retrieval=retrieval,mode=mode,error='')
            s['_model_advice']=dict(signature=advice_signature(s),report=report)
        return dict(session=s,reply=result['text'],sources=result['sources'],mode=mode)

    def retrieve(state):
        return dict(sources=retriever.search(store, state['text']))

    def answer(state):
        sources = state.get('sources', [])
        text, mode = format_evidence(state['text'], sources, state['session'].get('model_consent', False))
        return dict(reply=text, mode=mode)

    def risk(state):
        return dict(reply='这段描述需要优先由医务人员评估。请尽快联系当地急救或前往急诊，尤其是症状正在发生或加重时。'
            '\n\n本次常规追问已停止。你仍可核对和导出已填写的信息。这里的规则只是开发演示，不能完成临床分诊。',
            sources=[], mode='risk_notice')

    def boundary(state):
        s=state['session']
        reply='具体中药或中成药需要由中医师结合诊察与当前用药判断，这里不能替你选药、开方或确定剂量。'
        reply+='我可以结合你的描述整理中医问诊线索，解释相关知识及资料依据。'
        if s.get('_pending_question'):
            reply+='\n\n可以继续刚才的问题：'+s['_pending_question']
        else:
            reply+='你也可以询问某个中医概念或中药的一般知识。'
        return dict(reply=reply, sources=[], mode='boundary')

    graph = StateGraph(FlowState)
    def chat(state):
        return dict(reply=state['reply'],sources=[],mode=state['mode'])
    for name, fn in [('collect', collect), ('interview', interview), ('retrieve', retrieve),
                     ('answer', answer), ('risk', risk), ('boundary', boundary), ('advice', advice), ('chat',chat), ('online_chat',online_chat)]:
        graph.add_node(name, fn)
    graph.add_edge(START, 'collect')
    graph.add_conditional_edges('collect', lambda s: s['route'],
        {name: name for name in ['risk', 'boundary', 'retrieve', 'interview', 'advice','chat','online_chat']})
    graph.add_edge('retrieve', 'answer')
    for name in ['interview', 'answer', 'risk', 'boundary', 'advice','chat','online_chat']:
        graph.add_edge(name, END)
    return graph.compile()
