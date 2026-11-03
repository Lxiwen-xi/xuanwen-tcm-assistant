import json
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend import llm


@pytest.fixture
def client(tmp_path,monkeypatch):
    for k,v in {'ENABLE_LLM':'true','LLM_PROVIDER':'deepseek','DEEPSEEK_API_KEY':'synthetic-key'}.items():
        monkeypatch.setenv(k,v)
    return TestClient(create_app(tmp_path/'conversation.db'))


def output(reply='收到，你可以继续说。',**kw):
    return dict(intent='consultation',updates={},reply=reply,evidence_answer='',model_supplement=[],
        follow_up='',question_field='',**kw)


def start(client):
    return client.post('/api/sessions',json={'model_consent':True}).json()['id']


def say(client,sid,text):
    r=client.post(f'/api/sessions/{sid}/messages',json={'text':text})
    assert r.status_code==200,r.text
    return r.json()


def test_screenshot_multi_turn_context_is_sent_and_model_selects_questions(client,monkeypatch):
    calls=[]
    def request(system,user,**kw):
        d=json.loads(user);calls.append(d)
        text=d['current_text']
        if text=='我肚子不舒服':
            result=output('你说肚子不舒服，我先了解一下起病情况。')
            result.update(updates={'complaint':text},follow_up='这种不舒服从什么时候开始？',question_field='duration')
        elif text=='两天，可能有点吃坏肚子了':
            assert d['profile']['complaint']=='我肚子不舒服'
            assert d['pending_question']=='这种不舒服从什么时候开始？'
            assert any('我肚子不舒服' in m['content'] for m in d['recent_messages'])
            result=output('了解到是两天了；吃坏肚子目前只是你的猜测，还不能确定原因。')
            result.update(updates={'duration':'两天','details':'可能有点吃坏肚子了'},
                follow_up='现在主要是腹痛还是腹泻？有没有呕吐？',question_field='details')
        elif text=='腹痛，大概隔一个小时出现':
            result=output('你补充的是间隔约一小时出现腹痛。')
            result.update(updates={'frequency':'隔一个小时出现','details':'腹痛'},
                follow_up='每次不舒服大概持续多久？',question_field='details')
        else:
            assert text=='一个小时吧'
            assert d['pending_question']=='每次不舒服大概持续多久？'
            assert d['profile']['duration']=='两天'
            result=output('你是说每次不舒服大约持续一个小时，我会与两天的总病程分开记录。')
            result.update(updates={'details':'一个小时吧'},follow_up='不舒服具体在肚子的哪个位置？',question_field='details')
        return json.dumps(result,ensure_ascii=False)
    monkeypatch.setattr(llm,'_request',request)
    sid=start(client)
    for text in ['我肚子不舒服','两天，可能有点吃坏肚子了','腹痛，大概隔一个小时出现','一个小时吧']:
        s=say(client,sid,text)
        assert s['messages'][-1]['mode']=='online_chat'
        assert '我还不能确定这句话' not in s['messages'][-1]['content']
    assert len(calls)==4
    assert s['profile']['duration']=='两天'
    assert s['profile']['frequency']=='隔一个小时出现'
    assert '一个小时吧' in s['profile']['details']
    assert '六、' not in s['messages'][-1]['content']
    client.get('/api/sessions/'+sid)
    client.get('/api/sessions/'+sid+'/export')
    assert len(calls)==4


def test_model_not_forced_to_ask_missing_fields(client,monkeypatch):
    result=output('你只想要知识解释，我会先回答这个问题。')
    result.update(intent='knowledge',updates={'complaint':'你是谁'})
    monkeypatch.setattr(llm,'_request',lambda *a,**kw:json.dumps(result,ensure_ascii=False))
    s=say(client,start(client),'你是谁')
    assert s['messages'][-1]['content']==result['reply']
    assert not any(s['profile'].values()) and not s['last_slot']


def test_bad_extraction_does_not_discard_valid_answer(client,monkeypatch):
    result=output('两天的描述已收到；吃坏肚子仍只是一个猜测。')
    result['updates']={'duration':'两天','history':'确认食物中毒'}
    monkeypatch.setattr(llm,'_request',lambda *a,**kw:json.dumps(result,ensure_ascii=False))
    s=say(client,start(client),'两天，可能有点吃坏肚子了')
    assert s['messages'][-1]['mode']=='online_chat'
    assert s['profile']['duration']=='两天' and not s['profile']['history']


def test_switch_model_applies_to_current_session(client,monkeypatch):
    sid=client.post('/api/sessions',json={}).json()['id']
    s=say(client,sid,'我肚子不舒服')
    before=s['profile']
    assert client.patch('/api/sessions/'+sid+'/model',json={'model_consent':True}).json()['profile']==before
    result=output('我会结合已有的肚子不舒服来理解“两天”。')
    result['updates']={'duration':'两天'}
    monkeypatch.setattr(llm,'_request',lambda *a,**kw:json.dumps(result,ensure_ascii=False))
    s=say(client,sid,'两天')
    assert s['messages'][-1]['mode']=='online_chat' and s['profile']['duration']=='两天'
    s=client.patch('/api/sessions/'+sid+'/model',json={'model_consent':False}).json()
    assert not s['model_consent']


@pytest.mark.parametrize('bad_reply',['建议服用某药10克。','你患有某种疾病。','参考资料[999]。'])
def test_unsafe_model_reply_or_fake_citation_rejected(client,monkeypatch,bad_reply):
    monkeypatch.setattr(llm,'_request',lambda *a,**kw:json.dumps(output(bad_reply),ensure_ascii=False))
    s=say(client,start(client),'我肚子不舒服')
    assert s['messages'][-1]['mode']=='chat_api_fallback'
    assert not any(s['profile'].values())


def test_provider_failure_is_visible_and_does_not_consume_pending(client,monkeypatch):
    sid=client.post('/api/sessions',json={}).json()['id']
    first=say(client,sid,'我睡不着')
    client.patch('/api/sessions/'+sid+'/model',json={'model_consent':True})
    def fail(*a,**kw): raise llm.ModelError('模型请求超时。')
    monkeypatch.setattr(llm,'_request',fail)
    s=say(client,sid,'两天，最近有点焦虑')
    assert s['profile']==first['profile'] and s['last_slot']==first['last_slot']
    assert s['messages'][-1]['mode']=='chat_api_fallback'
    assert '超时' in s['model_error']


def test_ambiguous_hour_gets_context_specific_clarification(monkeypatch):
    def request(system,user,**kw):
        assert '不能将本轮擅自认定' in system
        d=output('你补充了大约一个小时，这和两天的总病程要区分。')
        d.update(intent='clarification',follow_up='你指每次不舒服持续一小时，还是每隔一小时出现？',question_field='details')
        return json.dumps(d,ensure_ascii=False)
    monkeypatch.setattr(llm,'_request',request)
    r=llm.converse('一个小时吧',{'complaint':'肚子不舒服','duration':'两天'},[],
        '主要是疼、胀还是恶心？',[])
    assert not r['updates']
    assert '每次' in r['follow_up'] and '每隔' in r['follow_up']
