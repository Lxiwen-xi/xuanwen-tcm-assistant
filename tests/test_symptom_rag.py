import json
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.rag import educational_excerpt, symptom_query
from backend import llm

TEXT = '''【第45页】
24.3 不寐（失眠，包括入睡困难和睡眠维持困难；问诊需了解病程及伴随情况）
病机：本段为测试用的传统睡眠概念说明，不能据此认定个人证型。
证型
辨证要点
治法
参考方
某证型
某些症状
补益心脾
归脾汤
【第47页】
25.2 痞满（脘腹痞塞胀满，问诊需记录与进食和时间的关系）
病机：本段为测试用胀满概念资料，应结合具体信息进一步确认。
证型
辨证要点
治法
参考方
某证型
保和丸
'''


@pytest.fixture
def app_client(tmp_path,monkeypatch):
    monkeypatch.setenv('ENABLE_LLM','false')
    client=TestClient(create_app(tmp_path/'rag.db'))
    store=client.app.state.store
    store.put_document(dict(id='tcm-manual',title='测试中医手册',url='',version='test-v1',content=TEXT,
        active=1,builtin=0,filename='test.pdf'))
    return client


def start(client,text,online=False):
    sid=client.post('/api/sessions',json={'model_consent':online}).json()['id']
    return sid,client.post(f'/api/sessions/{sid}/messages',json={'text':text}).json()


def test_symptom_synonyms_and_negation():
    q=symptom_query({'complaint':'我耳鸣，但没有失眠','associated':'没有头痛'})
    assert '耳鸣' in q and '不寐' not in q and '头痛' not in q
    assert '不寐' in symptom_query({'complaint':'我睡不着'})


def test_table_and_instruction_filter():
    excerpt=educational_excerpt(TEXT.split('【第47页】')[0])
    assert '不寐' in excerpt
    assert '归脾汤' not in excerpt and '补益心脾' not in excerpt
    assert not educational_excerpt('【第25页】\n药名\n性味归经\n功效\n某药药性说明')


def test_offline_symptom_retrieves_manual_with_page_and_export(app_client):
    sid,s=start(app_client,'我一周每晚睡不着')
    sources=s['advice']['sources']
    manual=next(x for x in sources if x['document_id']=='tcm-manual')
    assert manual['page']==45 and '不寐' in manual['text']
    assert s['advice']['retrieval']['external_hits']>=1
    assert '归脾汤' not in s['messages'][-1]['content']
    exported=app_client.get(f'/api/sessions/{sid}/export').text
    assert '测试中医手册 · 第45页' in exported


def test_manual_deactivate_invalidates_context(app_client):
    sid,s=start(app_client,'我一周每晚睡不着')
    assert any(x['document_id']=='tcm-manual' for x in s['advice']['sources'])
    app_client.patch('/api/documents/tcm-manual',json={'active':False})
    s=app_client.get('/api/sessions/'+sid).json()
    assert all(x['document_id']!='tcm-manual' for x in s['advice']['sources'])
    app_client.patch('/api/documents/tcm-manual',json={'active':True})
    app_client.delete('/api/documents/tcm-manual')
    assert all(x['document_id']!='tcm-manual' for x in app_client.get('/api/sessions/'+sid).json()['advice']['sources'])


def test_profile_edit_refreshes_symptom_retrieval(app_client):
    sid,s=start(app_client,'我睡不着')
    profile=dict(s['profile'],complaint='我胃胀')
    s=app_client.patch(f'/api/sessions/{sid}/profile',json=profile).json()
    assert '痞满' in s['advice']['retrieval']['query']
    assert any(x.get('page')==47 for x in s['advice']['sources'])
    assert not any(x.get('page')==45 for x in s['advice']['sources'])


def test_no_match_is_explicit(app_client):
    for d in app_client.get('/api/documents').json():
        app_client.patch('/api/documents/'+d['id'],json={'active':False})
    _,s=start(app_client,'xyzqqq测试未知症状')
    assert s['advice']['retrieval']['status']=='no_match'
    assert '未命中足够相关' in s['messages'][-1]['content']


def test_deepseek_receives_and_cites_manual_context(app_client,monkeypatch):
    for k,v in {'ENABLE_LLM':'true','LLM_PROVIDER':'deepseek','DEEPSEEK_API_KEY':'fake-test'}.items():
        monkeypatch.setenv(k,v)
    seen=[]
    def request(system,user,**kwargs):
        payload=json.loads(user);seen.append(payload)
        manual=next(x for x in payload['sources'] if x['document_id']=='tcm-manual')
        assert manual['page']==45
        assert '归脾汤' not in json.dumps(payload,ensure_ascii=False)
        rid=manual['id']
        return json.dumps(dict(intent='consultation',updates={'complaint':'我一周每晚睡不着','duration':'一周','frequency':'每晚'},
            reply='你描述了一周每晚睡不着。',evidence_answer=f'手册把不寐作为睡眠相关概念介绍，不能据此判断个人证候。[{rid}]',
            model_supplement=[],follow_up='白天状态受到什么影响？',question_field='details'),ensure_ascii=False)
    monkeypatch.setattr(llm,'_request',request)
    sid,s=start(app_client,'我一周每晚睡不着',online=True)
    assert s['messages'][-1]['mode']=='online_chat'
    assert '手册把不寐' in s['messages'][-1]['content']
    assert s['advice']['retrieval']['external_hits']>=1
    assert len(seen)==1
    app_client.get('/api/sessions/'+sid)
    app_client.get(f'/api/sessions/{sid}/export')
    assert len(seen)==1
    app_client.patch('/api/documents/tcm-manual',json={'active':False})
    s=app_client.get('/api/sessions/'+sid).json()
    assert s['advice'].get('mode')!='online_chat'
    assert all(x['document_id']!='tcm-manual' for x in s['advice']['sources'])
