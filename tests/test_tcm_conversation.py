import json
import pytest
from fastapi.testclient import TestClient
from backend import llm
from backend.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key,value in {'ENABLE_LLM':'true','LLM_PROVIDER':'deepseek','DEEPSEEK_API_KEY':'synthetic-key'}.items():
        monkeypatch.setenv(key,value)
    return TestClient(create_app(tmp_path/'tcm.db'))


def response(**kw):
    return dict(intent='consultation',updates={},reply='收到你的补充。',evidence_answer='',
                model_supplement=[],follow_up='',question_field='',**kw)


def send(client, sid, text):
    result=client.post(f'/api/sessions/{sid}/messages',json={'text':text})
    assert result.status_code==200
    return result.json()


@pytest.mark.parametrize('text',[
    '不建议自行服用中药。', '不要擅自停药。', '请勿口服来历不明的药物。',
    '我不能建议你服用某种药物。', '避免自行换药。',
])
def test_negated_medication_warning_is_not_a_prescription(text):
    assert llm.safe_output(text)


@pytest.mark.parametrize('text',[
    '建议服用某药。', '不建议吃甲药，但可以服用乙药。',
    '不要擅自停药；建议口服乙药。', '不建议自行服用10克某药。',
    '建议不要随意用药，但是你需要口服乙药。',
])
def test_negation_does_not_hide_an_affirmative_medication_instruction(text):
    assert not llm.safe_output(text)


def test_normal_denial_and_medication_warning_survive_validation(client, monkeypatch):
    d=response()
    d.update(updates={'associated':'没有呕吐'},model_supplement=['不建议自行服用中药。'],
             tcm_notes='可继续了解饮食与二便情况，这些是中医问诊的信息维度。',
             evidence_answer=None,follow_up=None,question_field=None)
    monkeypatch.setattr(llm,'_request',lambda *a,**kw:json.dumps(d,ensure_ascii=False))
    sid=client.post('/api/sessions',json={'model_consent':True}).json()['id']
    s=send(client,sid,'没有呕吐')
    assert s['messages'][-1]['mode']=='online_chat_supplement'
    assert s['profile']['associated']=='没有呕吐'
    assert '未获本知识库支持' in s['messages'][-1]['content']
    assert not s.get('model_error')


@pytest.mark.parametrize('question',['你推荐我吃什么中药','我应该吃哪种中成药','你建议我用什么中药'])
def test_personal_chinese_medication_request_gets_normal_boundary_not_api_error(client,monkeypatch,question):
    def fail(*a,**kw): raise AssertionError('This personal medication request should be handled locally')
    monkeypatch.setattr(llm,'_request',fail)
    sid=client.post('/api/sessions',json={'model_consent':True}).json()['id']
    s=send(client,sid,question)
    assert s['messages'][-1]['mode']=='boundary'
    assert '中医师' in s['messages'][-1]['content'] and not s.get('model_error')


def test_tcm_fields_context_sources_and_export_preserve_explicit_negatives(client,monkeypatch):
    calls=[]
    def request(system,user,**kw):
        data=json.loads(user);calls.append(data)
        guide=next(s for s in data['sources'] if s['document_id']=='builtin-diet')
        assert '中医问诊信息整理' in system
        d=response()
        if len(calls)==1:
            d.update(updates={'complaint':'肚子不舒服','associated':'没有呕吐','cold_heat':'怕冷',
                             'stool_urine':'大便偏稀'},
                     tcm_notes=f'中医问诊会了解寒热、饮食和大小便，需结合描述采集信息。[{guide["id"]}]',
                     follow_up='这种不适和进食有关系吗？',question_field='diet_thirst')
        else:
            assert data['profile']['stool_urine']=='大便偏稀'
            d.update(updates={'associated':'没有发热','diet_thirst':'吃饭后会胀','stool_urine':'每天两次'})
        return json.dumps(d,ensure_ascii=False)
    monkeypatch.setattr(llm,'_request',request)
    sid=client.post('/api/sessions',json={'model_consent':True}).json()['id']
    first=send(client,sid,'肚子不舒服，怕冷，大便偏稀，没有呕吐')
    assert '中医问诊关注点' in first['messages'][-1]['content']
    assert any(s['document_id']=='builtin-diet' for s in first['messages'][-1]['sources'])
    s=send(client,sid,'吃饭后会胀，每天两次，没有发热')
    assert '没有呕吐' in s['profile']['associated'] and '没有发热' in s['profile']['associated']
    assert '大便偏稀' in s['profile']['stool_urine'] and '每天两次' in s['profile']['stool_urine']
    assert not s['profile']['sleep_emotion']
    assert s['profile']['cold_heat']=='怕冷'
    text=client.get(f'/api/sessions/{sid}/export').text
    assert '中医问诊信息（用户自述）' in text and '饮食、口味与口渴：吃饭后会胀' in text
    assert '睡眠与情绪：未提供' in text
    assert len(calls)==2


def test_tcm_notes_cannot_invent_a_reference_or_personal_diagnosis(monkeypatch):
    for notes in ['你属于脾虚证。','中医资料已说明[999]。']:
        monkeypatch.setattr(llm,'_request',lambda *a,**kw:json.dumps(response(tcm_notes=notes),ensure_ascii=False))
        with pytest.raises(llm.ModelError):
            llm.converse('肚子不舒服',{},[],'',[])


def test_chat_does_not_force_tcm_interpretation(monkeypatch):
    d=response(tcm_notes='这里不应插入任何中医分析。');d['intent']='chat'
    monkeypatch.setattr(llm,'_request',lambda *a,**kw:json.dumps(d,ensure_ascii=False))
    r=llm.converse('你好',{},[],'',[])
    assert '中医分析' not in r['text'] and not r['updates']
