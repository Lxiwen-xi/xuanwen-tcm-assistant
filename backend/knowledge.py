"""有来源的短篇整理稿 + 中文 TF-IDF 检索；分数不是医学置信度。"""
from io import BytesIO
import re
from urllib.parse import urlparse
from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

SAMR = 'https://www.samr.gov.cn/bzjss/bzjd/art/2022/art_d96f4e909dd04ed082b20b5fdf87e84a.html'
WHO = 'https://www.who.int/news/item/18-01-2024-who-releases-ai-ethics-and-governance-guidance-for-large-multi-modal-models'

# 以下为开发者用自己的语言整理的短篇介绍，非原文全文，也未经临床审核。
# 内容限制在方法、信息采集及系统边界，不提供辨证结论和治疗方案。
SEEDS = [
    ('four', '中医四诊与文字问诊的范围', SAMR,
     '中医诊察包含望诊、闻诊、问诊和切诊。文字交流能够采集问诊相关信息，但不能完成完整四诊。望诊包含视觉观察，闻诊涉及声音和气味，切诊涉及触摸、按压等检查。单凭聊天描述不能代替医生综合诊察。'),
    ('intake', '问诊应整理哪些信息', SAMR,
     '问诊通过询问本人或陪诊者，了解不适如何开始、如何发展、当前表现以及已经接受的治疗等情况。整理就诊资料时，可记录主要不适、开始时间、变化和相关处理经过。没有询问或用户没有提供的项目，应保留为未明确。'),
    ('sleep', '睡眠与情绪：问诊信息分类', SAMR,
     '中医问诊标准把睡眠相关症和情绪相关症列为常见症分类。睡眠、情绪等描述是问诊信息的一部分，需要结合具体情况继续询问；某个孤立表现不足以直接确定证候。就诊前可以把睡眠不适的持续时间、发生频率及伴随情况按时间整理清楚。'),
    ('diet', '饮食、疼痛与其他问诊分类', SAMR,
     '中医问诊标准所介绍的常见症类别包含寒热、汗出、疼痛、头身胸腹不适、饮食与口味、大小便、情绪、睡眠等。分类用于帮助组织询问内容，不是根据单一症状自动判断疾病的对照表。'),
    ('summary', '就诊信息整理与四诊合参', SAMR,
     '四诊标准旨在规范临床信息的收集。文字问诊摘要可以记录用户的主诉、时间经过和已确认信息，作为就诊沟通的辅助材料。摘要应区分本人描述、系统整理和仍需医生确认的内容，不能当作医生出具的诊断报告。'),
    ('ai', 'AI 健康信息的使用边界', WHO,
     '世界卫生组织指出，生成式人工智能在健康领域可能产生错误、不完整或带偏差的信息。开发中应明确任务范围，重视隐私、可靠性和相关人员的参与。模型输出与资料引用仍需核对，不能仅凭流畅文字或引用编号判断其正确性。'),
]


def seed(store):
    if store.initialized():
        return
    existing = {d['id'] for d in store.documents()}
    for key, title, url, content in SEEDS:
        key = 'builtin-' + key
        if key not in existing:
            store.put_document(dict(id=key, title=title, url=url, content=content,
                version='整理稿 v1 · 未经临床审核', active=1, builtin=1, filename='内置整理稿'))
    store.mark_initialized()


def safe_url(url):
    parsed = urlparse(url)
    return url if parsed.scheme in {'http', 'https'} and parsed.netloc else ''


def parse_upload(filename, raw):
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError('单份资料上限为 5 MB。')
    suffix = filename.rsplit('.', 1)[-1].lower()
    if suffix == 'pdf':
        reader = PdfReader(BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError('暂不支持加密 PDF。')
        if len(reader.pages) > 150:
            raise ValueError('PDF 请控制在 150 页内。')
        text = '\n\n'.join(f'【第{i+1}页】\n{p.extract_text() or ""}' for i, p in enumerate(reader.pages))
        if not re.sub(r'【第\d+页】|\s', '', text):
            raise ValueError('未提取到文字，扫描 PDF 请先进行 OCR。')
    elif suffix in {'txt', 'md'}:
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('gb18030')
    else:
        raise ValueError('只支持 PDF、TXT、Markdown。')
    if not text.strip() or len(text) > 300000:
        raise ValueError('资料为空或文字超过 30 万字，请分开导入。')
    return text.strip()


class Retriever:
    def __init__(self):
        self.signature = None
        self.chunks = []

    def rebuild(self, docs, purpose='knowledge'):
        self.chunks = []
        for d in docs:
            # 保留页码与段落边界；长段落使用小量重叠。
            page = None
            chapter = ''
            page_sections = re.split(r'(?=【第\d+页】)', d['content'])
            is_paged = bool(re.search(r'【第\d+页】', d['content']))
            paragraphs = page_sections if is_paged else re.split(r'\n\s*\n', d['content'])
            for para in paragraphs:
                match = re.search(r'【第(\d+)页】', para)
                if match:
                    page = int(match[1])
                headings = list(re.finditer(r'第\d+章[^\n]+', para))
                section = chapter
                if headings and headings[0].start() < 180:
                    section = headings[0].group()
                if headings:
                    chapter = headings[-1].group()
                # 普通短 PDF 页面完整保留，避免把表格行切成不相干的片段。
                # 超长页仍需切分；不宣称已重建 PDF 表格结构。
                size, step = (2400, 2280) if is_paged else (420, 360)
                for start in range(0, len(para), step):
                    text = para[start:start + size].strip()
                    if purpose == 'symptom':
                        from .rag import educational_excerpt
                        text = educational_excerpt(text)
                    if text:
                        pieces = re.split(r'\n\s*\n',text) if purpose == 'symptom' else [text]
                        for piece in pieces:
                            if not piece.strip() or (purpose=='symptom' and len(piece.strip())<15):
                                continue
                            self.chunks.append(dict(document_id=d['id'], title=d['title'], url=safe_url(d['url']),
                                version=d['version'], page=page, section=section, text=piece.strip(), builtin=bool(d['builtin'])))
        self.matrix = None
        if self.chunks:
            self.vectorizer = TfidfVectorizer(analyzer='char', ngram_range=(1, 3), max_features=30000)
            self.matrix = self.vectorizer.fit_transform([c['title'] + ' ' + c['section'] + ' ' + c['text'] for c in self.chunks])

    def search(self, store, question, k=3, purpose='knowledge'):
        docs = store.documents(active_only=True)
        # 个人问诊摘要只用内置的信息采集资料，外部手册用于独立概念查询。
        if purpose == 'intake':
            docs = [d for d in docs if d['builtin']]
        signature = (purpose, tuple((d['id'], d['content'], d['title'], d['version'], d['url']) for d in docs))
        if signature != self.signature:
            self.rebuild(docs, purpose=purpose)
            self.signature = signature
        if self.matrix is None:
            return []
        query = re.sub(r'^(?:请|帮我|给我|介绍一下|解释一下|介绍|解释|什么是)+', '', question.strip())
        query = re.sub(r'(?:是什么|是什么意思|的知识|的概念|[？?。！!])+$', '', query).strip() or question
        scores = cosine_similarity(self.vectorizer.transform([query]), self.matrix)[0]
        if purpose == 'symptom':
            terms = [t for t in query.split() if len(t)>=2]
            for i, chunk in enumerate(self.chunks):
                text = re.sub(r'\s+','',chunk['text'])
                scores[i] += min(0.3, 0.15*sum(term in text for term in terms))
                if re.search(r'小儿|婴幼儿|儿童',chunk['text'].split('\n')[0]) and '小儿' not in query:
                    scores[i] = 0
        if 3 <= len(query) <= 30:
            # 短概念完整命中时增加词面权重，避免长表格页的相关性被稀释。
            for i, chunk in enumerate(self.chunks):
                if query in re.sub(r'\s+', '', chunk['section'] + chunk['text']):
                    scores[i] += 0.25
        return [dict(self.chunks[i], id=n + 1, score=round(float(scores[i]), 4))
                for n, i in enumerate(sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k])
                if scores[i] >= 0.12]
