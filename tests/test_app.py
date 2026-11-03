from io import BytesIO
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from backend.main import create_app
from backend.workflow import risk_flags
from backend.knowledge import parse_upload
from backend.llm import format_evidence


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('ENABLE_LLM', 'false')
    return TestClient(create_app(tmp_path / 'test.db'))


def new(client, save=False):
    r = client.post('/api/sessions', json={'save': save})
    assert r.status_code == 201
    return r.json()['id']


def say(client, key, text):
    r = client.post(f'/api/sessions/{key}/messages', json={'text': text})
    assert r.status_code == 200, r.text
    return r.json()


def test_full_interview_correction_and_export(client):
    key = new(client)
    s = say(client, key, '我最近一周每晚入睡困难')
    assert s['profile']['duration'] == '一周'
    assert s['profile']['frequency'] == '每晚'
    assert s['last_slot'] == 'associated'
    s = say(client, key, '没有其他不适')
    assert s['last_slot'] == 'history'
    s = say(client, key, '跳过')
    assert s['profile']['history'] == '未明确（用户跳过）'
    s = say(client, key, '没用药，过敏情况不清楚')
    assert s['stage'] == 'summarized'
    assert len(s['asked']) == len(set(s['asked']))
    assert s['messages'][-1]['sources']
    s['profile']['duration'] = '两周（已核对）'
    r = client.patch(f'/api/sessions/{key}/profile', json=s['profile'])
    assert r.status_code == 200
    exported = client.get(f'/api/sessions/{key}/export')
    assert '两周（已核对）' in exported.text
    assert '不是诊断报告' in exported.text


def test_knowledge_does_not_overwrite_pending_answer(client):
    key = new(client)
    s = say(client, key, '入睡困难')
    assert s['last_slot'] == 'duration'
    s = say(client, key, '什么是中医四诊？')
    assert s['last_slot'] == 'duration'
    assert s['messages'][-1]['sources'][0]['url'].startswith('https://')
    s = say(client, key, '三天')
    assert s['profile']['duration'] == '三天'
    assert s['last_slot'] == 'frequency'


@pytest.mark.parametrize('text,expected', [
    ('我没有胸痛，也没有呼吸困难', False),
    ('去年胸痛，现在已经好了', False),
    ('什么是呼吸困难？', False),
    ('我没有胸痛，但是现在喘不上气', True),
    ('我现在胸痛', True),
    ('我妈妈现在叫不醒', True),
    ('以前胸痛，现在又出现胸痛', True),
])
def test_risk_context(text, expected):
    assert bool(risk_flags(text)) == expected


def test_risk_interrupt_and_boundary(client):
    key = new(client)
    s = say(client, key, '我现在胸痛并呼吸困难')
    assert s['stage'] == 'attention'
    assert s['messages'][-1]['mode'] == 'risk_notice'
    assert not s['last_slot']
    assert say(client, key, '什么是四诊')['messages'][-1]['mode'] == 'risk_notice'
    other = new(client)
    assert say(client, other, '给我开药方，应该吃多少克')['messages'][-1]['mode'] == 'boundary'


def test_only_saved_sessions_survive_restart(tmp_path):
    path = tmp_path / 'test.db'
    client = TestClient(create_app(path))
    temp, saved = new(client), new(client, save=True)
    say(client, saved, '什么是四诊')
    assert [r['id'] for r in client.get('/api/sessions').json()] == [saved]
    restarted = TestClient(create_app(path))
    assert restarted.get('/api/sessions/' + saved).status_code == 200
    assert restarted.get('/api/sessions/' + temp).status_code == 404
    assert restarted.delete('/api/sessions/' + saved).status_code == 200
    assert restarted.get('/api/sessions').json() == []


def test_upload_activation_removal_and_retrieval(client):
    for d in client.get('/api/documents').json():
        client.patch('/api/documents/' + d['id'], json={'active': False})
    uploaded = client.post('/api/documents', files={'file': ('notes.md', '独特演示术语青竹：这是一条用于检索测试的资料。'.encode())},
        data={'title': '演示资料', 'url': 'javascript:alert(1)'}).json()
    assert not uploaded['active']
    assert uploaded['url'] == ''
    key = new(client)
    assert say(client, key, '什么是青竹？')['messages'][-1]['mode'] == 'no_evidence'
    client.patch('/api/documents/' + uploaded['id'], json={'active': True})
    assert say(client, key, '什么是青竹？')['messages'][-1]['sources'][0]['document_id'] == uploaded['id']
    assert client.post('/api/documents/reindex').json()['chunks'] == 1
    client.delete('/api/documents/' + uploaded['id'])
    assert say(client, key, '什么是青竹？')['messages'][-1]['mode'] == 'no_evidence'


def test_input_limits_empty_scan_and_page(client):
    key = new(client)
    assert client.post(f'/api/sessions/{key}/messages', json={'text': '  '}).status_code == 422
    assert client.post(f'/api/sessions/{key}/messages', json={'text': 'x' * 2001}).status_code == 422
    assert client.post('/api/documents', files={'file': ('bad.exe', b'not text')}).status_code == 400
    with pytest.raises(ValueError):
        parse_upload('large.md', b'x' * (5 * 1024 * 1024 + 1))
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    buf = BytesIO()
    writer.write(buf)
    with pytest.raises(ValueError, match='OCR'):
        parse_upload('scan.pdf', buf.getvalue())


def test_model_permission_validation_and_api_fallback(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'openai_compatible')
    monkeypatch.setenv('ENABLE_LLM', 'true')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setenv('CHAT_MODEL', 'mock')
    source = [dict(id=1, text='文字不能完成完整四诊。')]
    import langchain_openai
    class Fake:
        calls = 0
        reply = '{"evidence_answer":"文字问诊不能代替完整四诊。[1]","model_supplement":[]}'
        def __init__(self, **kwargs): pass
        def bind(self, **kwargs): return self
        def invoke(self, messages):
            Fake.calls += 1
            return SimpleNamespace(content=Fake.reply)
    monkeypatch.setattr(langchain_openai, 'ChatOpenAI', Fake)
    assert format_evidence('四诊', source)[1] == 'offline'
    assert Fake.calls == 0
    assert format_evidence('四诊', source, True)[1] == 'online'
    Fake.reply = '你已确诊。[99]'
    assert format_evidence('四诊', source, True)[1] == 'validation_fallback'
    def fail(*args, **kwargs): raise RuntimeError('fake supplier failed')
    monkeypatch.setattr(Fake, 'invoke', fail)
    assert format_evidence('四诊', source, True)[1] == 'api_fallback'


def test_deleted_seed_stays_deleted_on_restart(tmp_path):
    path = tmp_path / 'test.db'
    c = TestClient(create_app(path))
    c.delete('/api/documents/builtin-four')
    again = TestClient(create_app(path))
    assert 'builtin-four' not in [d['id'] for d in again.get('/api/documents').json()]


def test_pdf_page_boundary_and_reference_metadata(client):
    from backend.knowledge import Retriever
    store = client.app.state.store
    doc = dict(id='paged', title='分页测试', url='', version='v1', active=1, builtin=0,
        filename='test.pdf', content='【第13页】\n青竹术语：' + '青竹完整说明。' * 80 + '\n\n【第14页】\n白鹭术语：白鹭资料。')
    store.put_document(doc)
    r = Retriever()
    hits = r.search(store, '青竹术语')
    hit = next(h for h in hits if h['document_id'] == 'paged' and h['page'] == 13)
    assert '第14页' not in hit['text']
    assert hit['text'].endswith('青竹完整说明。')
    doc['version'] = 'v2'
    store.put_document(doc)
    assert next(h for h in r.search(store, '青竹术语') if h['document_id'] == 'paged')['version'] == 'v2'


def test_reference_queries_are_separate_from_personal_summary(client):
    from backend.knowledge import Retriever
    store = client.app.state.store
    store.put_document(dict(id='manual', title='睡眠参考方演示', url='', version='test', active=1,
        builtin=0, filename='test.md', content='睡眠入睡困难参考方：本条只用于测试外部知识不能自动推送到个人问诊摘要。'))
    r = Retriever()
    assert any(h['document_id'] == 'manual' for h in r.search(store, '睡眠入睡困难参考方'))
    assert all(h['document_id'] != 'manual' for h in r.search(store, '睡眠入睡困难参考方', purpose='intake'))
    key = new(client)
    for text in ['我最近一周每晚入睡困难', '没有其他不适', '跳过', '跳过']:
        s = say(client, key, text)
    assert s['stage'] == 'summarized'
    assert all(h['document_id'] != 'manual' for h in s['messages'][-1]['sources'])


def test_theory_question_is_not_a_personal_diagnosis_request(client):
    key = new(client)
    assert say(client, key, '什么是八纲辨证？')['messages'][-1]['mode'] != 'boundary'
    assert say(client, key, '帮我辨证')['messages'][-1]['mode'] == 'boundary'


def test_short_concept_on_long_pdf_continuation_page(client):
    from backend.knowledge import Retriever
    store = client.app.state.store
    store.put_document(dict(id='long-concept', title='术语教材', url='', version='test', active=1,
        builtin=0, filename='book.pdf', content='【第16页】\n脉象说明。\n第13章 八纲辨证\n\n【第17页】\n八纲即阴阳、表里、寒热、虚实。'+'其他背景资料。'*180))
    hits = Retriever().search(store, '什么是八纲辨证？')
    assert any(h['document_id'] == 'long-concept' and h['page'] == 17 and '八纲即' in h['text'] for h in hits)
