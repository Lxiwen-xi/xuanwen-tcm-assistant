import json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend import llm
from backend.main import create_app


@pytest.fixture
def configured(monkeypatch):
    for key,value in {'ENABLE_LLM':'true','LLM_PROVIDER':'deepseek','DEEPSEEK_API_KEY':'synthetic-test-key',
        'DEEPSEEK_MODEL':'deepseek-flash','DEEPSEEK_BASE_URL':'https://api.deepseek.com'}.items():
        monkeypatch.setenv(key,value)


def transport(monkeypatch, handler):
    original = httpx.Client
    def factory(**kwargs):
        return original(**kwargs,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(llm.httpx, 'Client', factory)


def response(content):
    return httpx.Response(200,json={'choices':[{'finish_reason':'stop',
        'message':{'content':content,'reasoning_content':'private reasoning never returned'}}]})


def report_json(rag_id=None):
    return json.dumps(dict(summary='你描述了近期入睡困难，目前仍有信息未确认。',
        explanation='睡眠可能受作息、环境和压力等因素影响，不能据此确定个人原因。[1]',
        suggestions=['记录睡眠变化，尽量保持固定起床时间。[1]'],
        seek_care='影响白天生活、持续或加重时应就医评估，不能排除风险。[1]',
        tcm_notes='文字问诊只能提供部分信息，无法完成完整四诊。'+f'[{rag_id or 2}]',
        questions=['请补充持续时间和对白天生活的影响。'],citations=[1,2]+([rag_id] if rag_id else [])),ensure_ascii=False)


def test_deepseek_protocol_and_json(configured,monkeypatch):
    seen=[]
    def handle(req):
        seen.append(req)
        return response('{"ok":true}')
    transport(monkeypatch,handle)
    assert llm._request('只返回json','测试',json_mode=True)=='{"ok":true}'
    req=seen[0]
    assert str(req.url)=='https://api.deepseek.com/chat/completions'
    assert req.headers['Authorization']=='Bearer synthetic-test-key'
    payload=json.loads(req.content)
    assert payload['model']=='deepseek-flash'
    assert payload['thinking']=={'type':'disabled'}
    assert payload['response_format']=={'type':'json_object'}


@pytest.mark.parametrize('code,hint',[(401,'密钥无效'),(402,'余额不足'),(429,'过于频繁'),(503,'繁忙')])
def test_errors_do_not_echo_supplier_or_key(configured,monkeypatch,code,hint):
    transport(monkeypatch,lambda req:httpx.Response(code,text='synthetic-test-key private supplier error'))
    result=llm.test_connection()
    assert not result['ok'] and hint in result['message']
    assert 'synthetic-test-key' not in json.dumps(result)
    assert 'supplier' not in json.dumps(result)


def test_timeout_and_empty_response(configured,monkeypatch):
    def fail(req): raise httpx.ReadTimeout('private data',request=req)
    transport(monkeypatch,fail)
    assert '超时' in llm.test_connection()['message']


def test_blank_json_and_hallucinated_facts_rejected(configured,monkeypatch):
    transport(monkeypatch,lambda req:response('   '))
    assert not llm.plan_turn('我睡不着',{},[],[])['ok']


def test_inferred_profile_value_is_rejected(configured,monkeypatch):
    transport(monkeypatch,lambda req:response(json.dumps({'updates':{'history':'没有高血压'},
        'question_field':'duration','question':'持续多久了？'},ensure_ascii=False)))
    plan=llm.plan_turn('我睡不着',{},[],[])
    assert not plan['ok'] and not plan['updates']


def test_online_interview_report_and_cache(configured,monkeypatch,tmp_path):
    client=TestClient(create_app(tmp_path/'online.db'))
    calls=[]
    def handle(req):
        payload=json.loads(req.content);calls.append(payload)
        return response(json.dumps(dict(intent='consultation',updates={'complaint':'我睡不着','associated':'没有其他不适','history':'无既往病史','medicines':'没有用药'},
            reply='你提到睡不着，其他信息已按原话记录。',evidence_answer='',model_supplement=[],
            follow_up='这个入睡问题持续多久了？',question_field='duration'),ensure_ascii=False))
    transport(monkeypatch,handle)
    key=client.post('/api/sessions',json={'model_consent':True}).json()['id']
    s=client.post(f'/api/sessions/{key}/messages',json={'text':'我睡不着，没有其他不适，无既往病史，没有用药'}).json()
    assert s['profile']['history']=='无既往病史'
    assert s['messages'][-1]['mode']=='online_chat'
    assert '这个入睡问题持续多久了' in s['messages'][-1]['content']
    assert s['advice']['mode']=='online_chat'
    assert len(calls)==1
    assert '_model_advice' not in s
    assert '你提到睡不着' in client.get(f'/api/sessions/{key}').json()['advice']['text']
    assert '你提到睡不着' in client.get(f'/api/sessions/{key}/export').text
    assert len(calls)==1
    p=dict(s['profile'],complaint='我反酸胃胀')
    changed=client.patch(f'/api/sessions/{key}/profile',json=p).json()
    assert changed['advice']['topics']==['digestive']
    assert 'DeepSeek 整理' not in changed['advice']['text']


def test_no_consent_and_risk_do_not_call_model(configured,monkeypatch,tmp_path):
    client=TestClient(create_app(tmp_path/'permission.db'))
    def forbidden(req): raise AssertionError('Unexpected model call')
    transport(monkeypatch,forbidden)
    key=client.post('/api/sessions',json={}).json()['id']
    s=client.post(f'/api/sessions/{key}/messages',json={'text':'我睡不着'}).json()
    assert s['messages'][-1]['mode']=='offline_advice'
    key=client.post('/api/sessions',json={'model_consent':True}).json()['id']
    s=client.post(f'/api/sessions/{key}/messages',json={'text':'我现在呼吸困难'}).json()
    assert s['messages'][-1]['mode']=='risk_notice'


def test_api_fallback_is_visible(configured,monkeypatch,tmp_path):
    client=TestClient(create_app(tmp_path/'failure.db'))
    transport(monkeypatch,lambda req:httpx.Response(402,text='private error'))
    key=client.post('/api/sessions',json={'model_consent':True}).json()['id']
    s=client.post(f'/api/sessions/{key}/messages',json={'text':'我睡不着'}).json()
    assert s['messages'][-1]['mode']=='chat_api_fallback'
    assert '余额不足' in s['model_error']
    assert 'private error' not in json.dumps(s)


def test_report_validation_keeps_local_baseline(configured,monkeypatch):
    from backend.advice import build_advice
    base=build_advice({'profile':{'complaint':'我睡不着'}})
    d=json.loads(report_json());d['suggestions']=['建议服用某药10克。[1]']
    transport(monkeypatch,lambda req:response(json.dumps(d,ensure_ascii=False)))
    assert llm.compose_advice({},base,[])['mode']=='advice_validation_fallback'
    d=json.loads(report_json());d['citations']=[999]
    monkeypatch.setattr(llm,'_request',lambda *a,**kw:json.dumps(d,ensure_ascii=False))
    assert llm.compose_advice({},base,[])['mode']=='advice_validation_fallback'


def test_env_reload_without_exposing_secret(monkeypatch,tmp_path):
    for key in ['LLM_PROVIDER','DEEPSEEK_API_KEY','DEEPSEEK_MODEL','DEEPSEEK_BASE_URL','ENABLE_LLM']:
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setattr(llm,'ROOT',tmp_path)
    path=tmp_path/'.env'
    path.write_text('ENABLE_LLM=true\nLLM_PROVIDER=deepseek\nDEEPSEEK_API_KEY=\n',encoding='utf-8')
    assert not llm.enabled()
    path.write_text('ENABLE_LLM=true\nLLM_PROVIDER=deepseek\nDEEPSEEK_API_KEY=local-only-secret\n',encoding='utf-8')
    assert llm.enabled()
    assert 'local-only-secret' not in json.dumps(llm.public_config())
