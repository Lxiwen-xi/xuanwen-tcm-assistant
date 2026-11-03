"""轻量对话分流与字段匹配；只有健康描述或明确的追问回答才进入采集。"""
import re
from .advice import positive_match

DURATION = r'(?:[一二三四五六七八九十百两半\d]+\s*(?:个)?(?:分钟|小时|天|周|星期|个月|月|年)|昨天|前天|今天|刚才|刚刚|从小|多年|很久)'
FREQUENCY = r'每天|每晚|每周|偶尔|持续不断|时不时|反复|一直|间歇|总是|经常|一天\d+次|每隔.{1,8}一次'
SYMPTOMS = (r'不舒服|不适|症状|难受|失眠|睡不着|睡不好|入睡困难|早醒|多梦|'
    r'乏力|疲劳|没力气|没精神|头晕|眩晕|耳鸣|心慌|心悸|胸闷|气短|'
    r'咳嗽|干咳|咳痰|发热|发烧|低烧|流鼻涕|鼻塞|喷嚏|恶心|呕吐|反酸|烧心|'
    r'胃胀|腹胀|肚子胀|胀气|腹泻|拉肚子|便秘|黑便|尿频|尿急|口干|口苦|'
    r'盗汗|出汗|怕冷|畏寒|瘙痒|皮疹|麻木|(?:头|胃|腹|肚子|胸|背|腰|腿|关节|嗓子|咽喉).{0,3}(?:疼|痛)|疼痛')
KNOWLEDGE = r'什么是|是什么|是什么意思|区别|解释|介绍|科普|包括哪些|有哪些|为什么要|怎么理解'
QUESTION = r'为什么|为何|怎么|如何|原因|区别|是否|能否|能不能|可不可以|吗[？?]?\s*$|[？?]'
SHORT_UNKNOWN = r'^(?:不知道|不清楚|记不清|说不清|不确定|没注意|没有|无|有|是的|不是|没有了)[。！!]*$'


def knowledge_question(text):
    return bool(re.search(KNOWLEDGE, text))


def field_matches(key, value):
    """避免用任何一句原话填充持续时间/频率，非推断的未知与跳过也可记录。"""
    if re.fullmatch(SHORT_UNKNOWN, value) or value in {'跳过','不想说'}:
        return True
    if key=='duration':
        return bool(re.fullmatch(r'(?:大概|大约|差不多|已经|持续|从|最近|有)?\s*'+DURATION+r'(?:了|左右|开始|前开始|开始的|才开始)?[。！!\s]*',value))
    if key=='frequency':
        return bool(re.fullmatch(r'(?:大概|大约|差不多)?(?:'+FREQUENCY+r')(?:发生|出现|这样|都有|会有|一次)?[。！!\s]*',value))
    patterns = {'duration':DURATION, 'frequency':FREQUENCY,
        'associated':SYMPTOMS+r'|伴随|其他|没有|并无|不伴',
        'history':r'病史|既往|以前|过去|手术|高血压|糖尿病|哮喘|没有|并无|健康',
        'medicines':r'药|过敏|服用|口服|正在用|没用|没有|不吃|不服'}
    return bool(re.search(patterns.get(key, r'(?!)'), value))


def analyze_turn(text, profile, slot):
    clean = re.sub(r'[\s，。！？,.!?～~]','',text).lower()
    # 完整的社交/功能提问独立回答；混有实际症状的句子仍继续向下分析。
    if re.fullmatch(r'(?:你好|您好|嗨|哈喽|hello|hi|在吗|有人吗|早上好|晚上好|你好呀|你好啊)',clean):
        return dict(kind='chat',reply='你好，我是铉问。你可以直接问中医或健康知识，也可以描述身体不适，我会根据你的问题来回答。')
    if re.search(r'你是谁|你叫什么|你的名字|你是干什么的|你是什么助手',text) and not re.search(SYMPTOMS,text):
        return dict(kind='chat',reply='我是铉问，一款中医知识与就诊前信息整理助手。可以查询知识、解释一般健康信息，并在你描述不适时整理情况和提供一般建议。你不需要按固定流程提问。')
    if re.search(r'你能做什么|你会什么|有什么功能|怎么用|如何使用|使用方法|帮助|你能帮我',text) and not re.search(SYMPTOMS,text):
        return dict(kind='chat',reply='你可以直接问知识问题，也可以描述身体不适；我会按内容选择回答方式。问诊中可以随时插入问题，相关信息仍会保留；右侧信息支持手工核对，也能导出摘要。')
    if re.fullmatch(r'(?:谢谢|谢谢你|好的|好|知道了|明白了|嗯|嗯嗯|再见|拜拜|不用了|没事了)',clean):
        return dict(kind='chat',reply='好的，需要时可以继续提问或补充情况。')
    if re.search(r'先不问了|不想问诊|暂停问诊|停止问诊|先别问|不要再问',text):
        return dict(kind='chat',reply='好的，先暂停追问。你可以直接问其他问题，或者说“继续问诊”再继续。',pause=True)
    if re.fullmatch(r'(?:继续|继续问诊|接着问|继续提问)[。！!]*',text):
        return dict(kind='resume')
    has_symptom = positive_match(SYMPTOMS,text)
    personal = bool(re.search(r'我|本人|孩子|妈妈|爸爸|家人|最近|这几天|现在',text))
    generic = bool(re.search(r'如果|假如|举个例子|我想了解|我想学习|我想知道|我想问',text))
    if generic or (knowledge_question(text) and not (personal and has_symptom)):
        return dict(kind='knowledge')
    # 提问不等于回答追问，尤其是“为什么问病史”“一天几次算尿频”。
    if re.search(QUESTION,text) and not (personal and positive_match(SYMPTOMS,text)):
        return dict(kind='knowledge')
    updates = {}
    duration = re.search(DURATION,text)
    frequency = re.search(FREQUENCY,text)
    duration_answer = re.fullmatch(r'(?:大概|大约|差不多|已经|持续|从|最近|有)?\s*'+DURATION+r'(?:了|左右|开始|前开始|开始的|才开始)?[。！!\s]*',text)
    if duration and (has_symptom or duration_answer): updates['duration']=duration.group()
    if frequency and (has_symptom or re.fullmatch(r'(?:大概|大约|差不多)?(?:'+FREQUENCY+r')(?:发生|出现|这样|都有|会有|一次)?[。！!\s]*',text)):
        updates['frequency']=frequency.group()
    if has_symptom:
        if profile.get('complaint') and slot=='associated':
            updates['associated']=text[:500]
        else:
            updates['complaint']=text[:500]
        return dict(kind='intake',updates=updates)
    if profile.get('complaint'):
        # 明确的字段描述可打断当前追问，写回正确字段，而不占用错误字段。
        for key in ('history','medicines','associated'):
            anchors={'history':r'病史|既往|手术', 'medicines':r'(?:用|吃|服).{0,3}药|过敏',
                     'associated':r'没有其他|没有别的|无其他|没有伴随'}
            if re.search(anchors[key],text): updates[key]=text[:500]
        if slot and field_matches(slot,text) and not updates:
            updates[slot]=text[:500]
        if updates:
            return dict(kind='intake',updates=updates)
    return dict(kind='unclear',updates={})


def repair_legacy_profile(session):
    """修正旧流程明确误填的社交语，保留原值备份，不重写正常健康描述。"""
    if session.get('_intent_version')==1: return False
    session['_intent_version']=1
    p=session['profile']
    before=dict(p)
    invalid_chief=False
    for key,value in list(p.items()):
        if value and analyze_turn(value,{},None)['kind']=='chat':
            p[key]=''
            if key=='complaint': invalid_chief=True
    if invalid_chief and p.get('associated'):
        actual=analyze_turn(p['associated'],{},None)
        if actual['kind']=='intake' and actual['updates'].get('complaint'):
            p['complaint']=p['associated']
            p['associated']=''
            for key in ('duration','frequency'):
                if not p.get(key): p[key]=actual['updates'].get(key,'')
    if p!=before:
        session['_profile_before_intent_fix']=before
        for key in ('_model_advice','_rag_context','_turn_plan'): session.pop(key,None)
        session['asked']=[]
        session['last_slot']=None
        if session.get('stage')!='attention': session['stage']='collecting' if p.get('complaint') else 'new'
    return True
