import json
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend import llm, workflow


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('ENABLE_LLM','false')
    return TestClient(create_app(tmp_path/'intent.db'))


def new(client):
    return client.post('/api/sessions',json={}).json()['id']


def say(client,sid,text):
    r=client.post(f'/api/sessions/{sid}/messages',json={'text':text})
    assert r.status_code==200,r.text
    return r.json()


@pytest.mark.parametrize('text',['你是谁','你好','你叫什么名字？','你能做什么','怎么用？','谢谢','Hello','你好呀'])
def test_non_symptom_openings_do_not_start_interview(client,text):
    s=say(client,new(client),text)
    assert s['messages'][-1]['mode']=='conversation'
    assert not any(s['profile'].values()) and not s['asked'] and not s['last_slot']
    assert s['stage']=='new' and s['advice'] is None


def test_screenshot_sequence_no_longer_pollutes_fields(client):
    sid=new(client)
    s=say(client,sid,'你是谁')
    assert '铉问' in s['messages'][-1]['content']
    s=say(client,sid,'你是谁')
    assert not any(s['profile'].values())
    s=say(client,sid,'偶尔会肚子不舒服')
    assert s['profile']['complaint']=='偶尔会肚子不舒服'
    assert s['profile']['frequency']=='偶尔'
    assert not s['profile']['duration'] and s['last_slot']=='duration'
    s=say(client,sid,'我有点肚子不舒服')
    assert '肚子不舒服' in s['profile']['complaint']
    assert not s['profile']['duration'] and not s['profile']['associated']
    assert s['last_slot']=='duration'


@pytest.mark.parametrize('text',['你是谁','你好','谢谢','今天天气真好','我想考研','为什么需要问持续时间？'])
def test_interruptions_preserve_pending_slot_and_profile(client,text):
    sid=new(client)
    first=say(client,sid,'我睡不着')
    s=say(client,sid,text)
    assert s['profile']==first['profile'] and s['asked']==first['asked']
    assert s['last_slot']=='duration'
    s=say(client,sid,'三天')
    assert s['profile']['duration']=='三天'
    assert s['last_slot']=='frequency'


def test_out_of_order_answer_goes_to_correct_field(client):
    sid=new(client)
    say(client,sid,'我肚子不舒服')
    s=say(client,sid,'偶尔')
    assert s['profile']['frequency']=='偶尔' and not s['profile']['duration']
    assert s['last_slot']=='duration'
    s=say(client,sid,'有高血压病史')
    assert s['profile']['history']=='有高血压病史' and not s['profile']['duration']
    s=say(client,sid,'三天')
    assert s['profile']['duration']=='三天' and s['last_slot']=='associated'


def test_new_symptom_not_filled_into_history(client):
    sid=new(client)
    say(client,sid,'我一周每晚睡不着')
    s=say(client,sid,'没有其他不适')
    assert s['last_slot']=='history'
    s=say(client,sid,'我还有点肚子疼')
    assert not s['profile']['history'] and s['last_slot']=='history'
    assert '肚子疼' in s['profile']['complaint']


def test_unknown_text_is_clarified_without_model(client):
    s=say(client,new(client),'asdfg123')
    assert s['messages'][-1]['mode']=='clarification'
    assert not any(s['profile'].values())


def test_knowledge_and_mixed_symptom_inputs(client):
    s=say(client,new(client),'失眠怎么办？')
    assert not any(s['profile'].values())
    s=say(client,new(client),'你好，我肚子不舒服，有什么建议？')
    assert '肚子不舒服' in s['profile']['complaint']
    assert s['advice'] is not None


def test_pause_and_resume_do_not_consume_slot(client):
    sid=new(client)
    first=say(client,sid,'我睡不着')
    s=say(client,sid,'先别问')
    assert s['profile']==first['profile'] and s['last_slot']=='duration'
    s=say(client,sid,'继续问诊')
    assert s['last_slot']=='duration' and '持续多久' in s['messages'][-1]['content']


def test_online_messages_all_use_conversation_model(client,monkeypatch):
    # Enable the already-created isolated session without any real provider request.
    monkeypatch.setenv('ENABLE_LLM','true')
    monkeypatch.setenv('LLM_PROVIDER','deepseek')
    monkeypatch.setenv('DEEPSEEK_API_KEY','synthetic-test-key')
    sid=client.post('/api/sessions',json={'model_consent':True}).json()['id']
    calls=[]
    def converse(text,profile,recent,pending,sources):
        calls.append(text)
        return dict(text='根据本轮问题生成的回答。',intent='chat',updates={},follow_up='',question_field='',sources=[],supplement=False)
    monkeypatch.setattr(workflow,'converse',converse)
    say(client,sid,'你是谁')
    assert calls==['你是谁']
    s=say(client,sid,'谈谈五运六气')
    assert calls==['你是谁','谈谈五运六气']
    assert not any(s['profile'].values())


@pytest.mark.parametrize('data',[
    {'intent':'intake','updates':{'duration':'你是谁'}},
    {'intent':'intake','updates':{'history':'未说过的病史'}},
    {'intent':'knowledge','updates':{'complaint':'你是谁'}},
    {'intent':'intake','updates':{}},
])
def test_semantic_router_rejects_bad_updates(monkeypatch,data):
    monkeypatch.setattr(llm,'_request',lambda *a,**kw:json.dumps(data,ensure_ascii=False))
    assert llm.classify_turn('你是谁',{},None,[])['kind']=='unclear'


def test_legacy_saved_profile_repairs_only_obvious_chat_pollution(client):
    s=client.post('/api/sessions',json={'save':True}).json()
    s['profile'].update(complaint='你是谁',duration='你是谁',frequency='偶尔',associated='偶尔会肚子不舒服')
    s['stage']='collecting'
    s['asked']=['duration','frequency']
    s['last_slot']='associated'
    client.app.state.store.save_session(s)
    fixed=client.get('/api/sessions/'+s['id']).json()
    assert fixed['profile']['complaint']=='偶尔会肚子不舒服'
    assert not fixed['profile']['duration'] and not fixed['profile']['associated']
    assert fixed['profile']['frequency']=='偶尔'
    assert '_profile_before_intent_fix' not in fixed
    stored=client.app.state.store.get_session(s['id'])
    assert stored['_profile_before_intent_fix']['complaint']=='你是谁'
    next_turn=say(client,s['id'],'三天')
    assert next_turn['profile']['duration']=='三天'


def test_no_consent_does_not_use_semantic_classifier(client,monkeypatch):
    def fail(*a,**kw): raise AssertionError('Unexpected model classification')
    monkeypatch.setattr(workflow,'converse',fail)
    assert say(client,new(client),'随机内容')['messages'][-1]['mode']=='clarification'
