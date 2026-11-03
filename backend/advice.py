"""有出处的症状解读卡片。离线规则整理，不推断诊断或个人中医证型。"""
import re
import json

CARDS = [
    dict(key='sleep', name='睡眠困扰', pattern=r'失眠|入睡困难|睡不着|睡不好|早醒|易醒|睡眠差',
         explanation='入睡困难、夜间反复醒来或早醒都属于睡眠困扰的不同表现。压力、睡眠环境、咖啡因或作息变化可能相关；疼痛、药物及其他睡眠问题也可能影响睡眠，不能仅凭这段描述确定原因。',
         steps=['尽量保持固定的起床时间，有困意时再上床。', '睡前安排安静的放松活动，减少临睡前使用电子屏幕，避免晚间大量进食和咖啡因。', '记录入睡时间、夜间醒来次数和白天状态；困倦时避免驾驶。'],
         care='若已影响白天生活、反复持续，或调整睡眠习惯仍无改善，应预约医生评估。若有打鼾伴憋醒，也应主动说明。',
         questions=['主要是入睡难、夜间醒来还是早醒？', '白天是否受影响，有无打鼾憋醒或近期作息变化？'],
         tcm='中医问诊会进一步了解睡眠、情绪、寒热和饮食等信息；这些维度有助于采集资料，不能据单一失眠表现认定某个证型。',
         url='https://www.nhs.uk/conditions/insomnia/', title='NHS：Insomnia'),
    dict(key='digestive', name='反酸、烧心或餐后胀满', pattern=r'反酸|烧心|胃胀|腹胀|消化不良|嗳气|打嗝',
         explanation='反酸、烧心、餐后饱胀和嗳气可能出现在消化不良或反流相关问题中，饮食、压力和某些药物可能有关。胃痛或腹痛不能简单当成消化不良，需要另外说明部位、程度与经过。',
         steps=['记录哪些食物或进食时间容易诱发，减少个人明确会加重症状的刺激因素。', '避免临睡前进食；进食后注意症状与体位的关系。', '整理目前药物清单供医生或药师核对，不自行停药或叠加中西药。'],
         care='反复发作、持续加重、吞咽困难、反复呕吐或不明原因体重下降时应就医。若出现胸痛、呕血、黑便或剧烈持续腹痛，应优先及时寻求医疗评估。',
         questions=['是反酸烧心、餐后胀满，还是明确的疼痛？', '与进食或平卧有什么关系，有无吞咽困难、呕吐或体重变化？'],
         tcm='中医问诊可记录食欲、口味、饮食和二便等表现，仍需结合其他诊察；不能直接把胀满归结为“脾虚”。',
         url='https://www.nhs.uk/conditions/indigestion/', title='NHS：Indigestion'),
    dict(key='fatigue', name='乏力或持续疲劳', pattern=r'乏力|疲劳|疲倦|没精神|没力气|容易累',
         explanation='疲劳可能与睡眠不足、压力、生活方式、药物或身体疾病有关。反复或持续的乏力不能只用“虚”解释，也不能据此认定贫血或其他疾病。',
         steps=['记录疲劳持续多久、是否影响日常活动，以及睡眠和近期生活变化。', '整理当前用药和伴随表现，不自行购买补药或用食疗替代检查。'],
         care='原因不明并持续数周、影响生活，或伴体重变化、明显口渴尿频、心悸等，应就医寻找原因；伴胸痛、呼吸困难或意识变化时优先急诊评估。',
         questions=['是否睡够后仍疲倦，活动后会不会明显加重？', '有无体重变化、口渴尿频、心悸或用药变化？'],
         tcm='气虚、血虚等是中医理论中的不同概念；乏力是一个表现，不能单凭它确认某一种证型。',
         url='https://www.nhs.uk/symptoms/tiredness-and-fatigue/', title='NHS：Tiredness and fatigue'),
    dict(key='cough', name='咳嗽', pattern=r'咳嗽|干咳|咳痰',
         explanation='咳嗽可能与呼吸道感染、过敏、吸烟、反流等情况有关。干咳、有痰、发生时段和伴随情况需要分开了解，不能仅凭痰色确定病因或决定用药。',
         steps=['注意休息，记录体温、痰的情况和是否接触烟雾等刺激物。', '饮水以自身情况和既有医嘱为准；有发热或明显不适时减少与他人密切接触。'],
         care='持续超过三周、快速加重、明显不适或伴体重下降时应就医；咳血、胸痛或呼吸困难需要优先及时评估。',
         questions=['干咳还是有痰，夜间或运动时是否加重？', '有无发热、胸痛、咳血或呼吸困难？'],
         tcm='中医问诊会进一步了解寒热、汗出、痰和病程等信息；不能仅凭一次咳嗽描述确定风寒或风热。',
         url='https://www.nhs.uk/symptoms/cough/', title='NHS：Cough'),
    dict(key='headache', name='头痛', pattern=r'头痛|头疼',
         explanation='头痛可能与压力、饮食饮水、视力或其他因素有关，也可能需要进一步检查。位置、突然还是逐渐发生、严重程度和伴随症状比单一“头痛”描述更重要。',
         steps=['记录头痛的时间、部位、程度和可能诱因，避免长时间用眼，尽量规律进餐。', '如果疼痛明显、反复或与以往不同，安排医生评估，不自行判断为某种中医证型。'],
         care='突然出现极其剧烈的头痛，或伴肢体无力、说话困难、意识异常、视力丧失等，应立即急诊评估。头痛伴视力问题、呕吐或持续加重，也应尽快就医。',
         questions=['什么时候开始，突然还是逐渐发生，部位和程度如何？', '有无外伤、发热、视力变化、呕吐或肢体无力？'],
         tcm='中医可记录疼痛部位、性质、寒热和诱发因素；这些记录不能代替神经系统等必要的医学评估。',
         url='https://www.nhs.uk/symptoms/headaches/', title='NHS：Headaches'),
]
TCM_SOURCE = 'https://www.samr.gov.cn/bzjss/bzjd/art/2022/art_d96f4e909dd04ed082b20b5fdf87e84a.html'


def positive_match(pattern, text):
    for clause in re.split(r'[，。；\n]|但是|不过|但', text):
        for hit in re.finditer(pattern, clause):
            if not re.search(r'(?:没有|否认|不伴|未出现|无)[^，。]{0,3}$', clause[:hit.start()]):
                return True
    return False


def wants_advice(text):
    return bool(re.search(r'建议|分析|怎么办|怎么调理|怎么缓解|什么情况|什么病', text)) and not re.search(
        r'失眠|睡不|入睡|头痛|头疼|咳|反酸|烧心|胃|腹|乏力|疲劳|疼|痛|发热|呕吐|出血|喘', text)


def advice_signature(session):
    context = session.get('_rag_context') or {}
    return json.dumps(dict(answer_version='tcm-conversation-v3', profile=session.get('profile',{}), revision=context.get('revision'),
        query=context.get('query')), ensure_ascii=False, sort_keys=True)


def build_advice(session):
    p = session.get('profile', {})
    complaint = p.get('complaint', '').strip()
    if session.get('stage') == 'attention':
        return dict(text='优先就医提示\n\n'+session.get('risk_notice', '')+
            '\n请优先联系医务人员或急诊评估。本次不提供日常调理方案，以免延误。', sources=[], topics=[])
    if not complaint:
        return None
    cached = session.get('_model_advice')
    if session.get('model_consent') and cached and cached.get('signature') == advice_signature(session):
        return cached['report']
    cards = [c for c in CARDS if positive_match(c['pattern'], complaint)][:2]
    combined = '。'.join(p.values())
    special = positive_match(r'孕妇|怀孕|哺乳|婴儿|幼儿|宝宝|小孩|孩子|儿童|透析', combined)
    sources = []
    lines = ['症状解读与建议', '根据你提供的信息整理；以下是一般健康信息，不是诊断或个人处方。', '',
             '一、目前了解到的情况', f'主要描述：{complaint}',
             f'持续时间：{p.get("duration") or "未明确"}；发生频率：{p.get("frequency") or "未明确"}。',
             f'伴随情况：{p.get("associated") or "未提供，不能据此认为没有伴随症状"}。', '',
             '二、这些表现可能与什么有关']
    if cards:
        for i, c in enumerate(cards, 1):
            sources.append(dict(id=i, title=c['title'], url=c['url'], version='一般健康资料 · 2026-11-03核对',
                page=None, score=None, text=c['explanation']+'\n'+c['care'], builtin=True, document_id='advice-'+c['key']))
            lines.append(f'{c["name"]}：{c["explanation"]} [{i}]')
        lines += ['', '三、可以先做的事情']
        if special:
            lines.append('描述涉及儿童、孕哺或透析等需要单独评估的情况，请先向相应医务人员咨询。这里不直接套用成人的一般调理建议。')
        else:
            for i, c in enumerate(cards, 1):
                lines += [f'• {s} [{i}]' for s in c['steps']]
        lines += ['', '四、什么时候需要就医']
        for i, c in enumerate(cards, 1):
            lines.append(c['care']+f' [{i}]')
        lines += ['', '五、中医问诊还会关注什么']
        lines += [c['tcm'] for c in cards]
    else:
        contextual = bool((session.get('_rag_context') or {}).get('sources'))
        lines += [('当前没有该症状的预置健康建议卡，检索到的手册内容仅作为知识背景；具体原因仍需确认。' if contextual else
                   '目前没有匹配到经过整理的症状解释资料，因此不能可靠说明可能原因。需要先明确不适部位、程度、病程和伴随表现。'),
                  '', '三、现在可以做什么', '记录症状出现的时间、诱因、变化和已做处理；整理既往病史及当前用药，方便就诊沟通。',
                  '', '四、就医提示', '症状明显、持续、加重或原因不明时，应由医生评估。不要依据当前未命中的资料自行辨证、购药或停药。',
                  '', '五、中医问诊的范围', '文字信息只覆盖部分问诊内容，不能完成望闻问切。']
    tcm_id = len(sources) + 1
    sources.append(dict(id=tcm_id, title='市场监管总局：中医四诊标准解读', url=TCM_SOURCE,
        version='问诊信息采集依据', page=None, score=None, text='四诊包括望闻问切。问诊用于了解病情、发展及治疗经过；采集信息不等于确定个人诊断。',
        builtin=True, document_id='advice-tcm'))
    lines.append(f'以上中医问诊范围依据 [{tcm_id}]。')
    lines += ['', '六、仍需确认的信息']
    missing = [name for key, name in [('duration','持续时间'),('frequency','频率与变化'),('associated','伴随表现'),
               ('history','既往病史'),('medicines','用药及过敏情况')] if not p.get(key) or '未明确' in p[key] or '不清楚' in p[key]]
    lines.append('尚未明确：'+'、'.join(missing)+'。' if missing else '通用项目已有记录，具体原因仍需结合下面的针对性信息确认。')
    lines += ['• '+q for c in cards for q in c['questions']]
    if not cards:
        lines.append('• 最主要的不适是什么、发生在哪里、严重程度如何，是否影响日常活动？')
    context = session.get('_rag_context') or {}
    hits = context.get('sources',[])
    if context:
        lines += ['', '七、本次知识库参考']
        if hits:
            lines.append('以下为相关知识片段，不能据此认定你的个人证型；处方与治法表格已略过。')
            for h in hits:
                source = dict(h, id=len(sources)+1, excerpt_kind='educational')
                sources.append(source)
                where = f' · 第{h["page"]}页' if h.get('page') else ''
                lines.append(f'[{source["id"]}] {h["title"]}{where}\n{h["text"]}')
        else:
            lines.append('当前启用的知识资料未命中足够相关的可用片段；本次不将手册作为症状解释依据。')
    return dict(text='\n'.join(lines), sources=sources, topics=[c['key'] for c in cards],
                care_notes='\n'.join(c['care'] for c in cards), restricted=special,
                retrieval=dict(status=context.get('status','not_run'), hits=len(hits),
                    external_hits=context.get('external_hits',0), query=context.get('query','')))
