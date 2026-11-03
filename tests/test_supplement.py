import json
import pytest
from fastapi.testclient import TestClient
from backend import llm
from backend.advice import build_advice
from backend.main import create_app


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(llm, 'enabled', lambda: True)
    monkeypatch.setattr(llm, 'public_config', lambda: {'provider_label':'DeepSeek'})


def mock_reply(monkeypatch, data):
    monkeypatch.setattr(llm, '_request', lambda *a, **kw: json.dumps(data,ensure_ascii=False))


def test_missing_knowledge_uses_labeled_model_supplement(online,monkeypatch,tmp_path):
    client=TestClient(create_app(tmp_path/'supplement.db'))
    for doc in client.get('/api/documents').json():
        client.patch('/api/documents/'+doc['id'],json={'active':False})
    mock_reply(monkeypatch,dict(intent='knowledge',updates={},reply='',evidence_answer='',model_supplement=['该概念的一般解释仍需结合专业资料确认。'],follow_up='',question_field=''))
    # main.enabled uses the imported config function; authorize only this isolated test.
    monkeypatch.setenv('ENABLE_LLM','true')
    monkeypatch.setenv('DEEPSEEK_API_KEY','synthetic-test-key')
    monkeypatch.setenv('LLM_PROVIDER','deepseek')
    sid=client.post('/api/sessions',json={'model_consent':True}).json()['id']
    s=client.post(f'/api/sessions/{sid}/messages',json={'text':'什么是昼夜节律？'}).json()
    reply=s['messages'][-1]
    assert reply['mode']=='online_chat_supplement'
    assert '模型补充，未获本知识库支持' in reply['content']
    assert not reply['sources']
    assert '[' not in reply['content']


def test_no_authorization_never_calls_model(online,monkeypatch):
    def fail(*a,**kw): raise AssertionError('Must not call model')
    monkeypatch.setattr(llm,'_request',fail)
    assert llm.format_evidence('什么是昼夜节律？',[],False)[1]=='no_evidence'


def test_mixed_answer_keeps_sources_separate(online,monkeypatch):
    sources=[dict(id=1,text='问诊需要核对实际提供的信息。')]
    mock_reply(monkeypatch,dict(evidence_answer='问诊需要核对已提供的信息。[1]',model_supplement=['一般知识补充需要进一步核实。']))
    text,mode=llm.format_evidence('什么是问诊？',sources,True)
    assert mode=='online_rag_supplement'
    assert text.index('[1]')<text.index('模型补充，未获本知识库支持')


@pytest.mark.parametrize('item',['你患有某种疾病。','建议服用某药10克。','资料已核验可靠。','参考本知识库[1]。','来源https://invented.example'])
def test_unsafe_or_fake_attribution_supplements_rejected(online,monkeypatch,item):
    mock_reply(monkeypatch,dict(evidence_answer='',model_supplement=[item]))
    text,mode=llm.format_evidence('知识问题',[],True)
    assert mode=='validation_fallback'
    assert item not in text


def test_no_sources_cannot_claim_evidence(online,monkeypatch):
    mock_reply(monkeypatch,dict(evidence_answer='手册已确认此结论。[1]',model_supplement=[]))
    assert llm.format_evidence('知识问题',[],True)[1]=='validation_fallback'


def test_api_failure_preserves_no_evidence_fallback(online,monkeypatch):
    def fail(*a,**kw): raise llm.ModelError('模型暂时不可用。')
    monkeypatch.setattr(llm,'_request',fail)
    text,mode=llm.format_evidence('知识问题',[],True)
    assert mode=='api_fallback' and '未找到' in text


def test_advice_can_append_separate_supplement(online,monkeypatch):
    base=build_advice({'profile':{'complaint':'我睡不着'}})
    data=dict(summary='你描述了入睡困难，还有情况需要确认。',
        explanation='睡眠可能与作息和环境等因素有关，个人原因尚不能确定。[1]',
        suggestions=['保持规律起床并记录睡眠变化。[1]'],seek_care='持续加重或影响白天生活时应就医。[1]',
        tcm_notes='文字交流不能完成完整四诊，需要线下补充信息。[2]',questions=['持续时间及白天状态如何？'],
        citations=[1,2],model_supplement=['昼夜节律是理解睡眠的一般概念，具体情况需专业评估。'])
    mock_reply(monkeypatch,data)
    report=llm.compose_advice({},base,[])
    assert report['mode']=='online_supplement'
    assert '七、模型补充，未获本知识库支持' in report['text']
    assert report['sources']==base['sources']
    data['model_supplement']=['你患有某种疾病。']
    mock_reply(monkeypatch,data)
    assert llm.compose_advice({},base,[])['mode']=='advice_validation_fallback'
