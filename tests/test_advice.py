import re
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('ENABLE_LLM', 'false')
    return TestClient(create_app(tmp_path/'advice.db'))


def start(client, text):
    key = client.post('/api/sessions', json={}).json()['id']
    s = client.post(f'/api/sessions/{key}/messages', json={'text': text}).json()
    return key, s


@pytest.mark.parametrize('text,topic,expected', [
    ('我一周每晚入睡困难', 'sleep', '固定的起床时间'),
    ('我最近反酸胃胀', 'digestive', '临睡前进食'),
    ('我这几天一直乏力', 'fatigue', '疲劳可能与'),
    ('我干咳三天', 'cough', '接触烟雾'),
    ('我最近偶尔头痛', 'headache', '头痛的时间'),
])
def test_first_symptom_has_explanation_advice_and_sources(client, text, topic, expected):
    _, s = start(client, text)
    report = s['messages'][-1]
    assert report['mode'] == 'offline_advice'
    assert topic in s['advice']['topics']
    assert expected in report['content']
    assert '四、什么时候需要就医' in report['content']
    assert '接下来确认一项' in report['content']
    assert report['sources']
    ids = {int(i) for i in re.findall(r'\[(\d+)\]', report['content'])}
    assert ids.issubset({x['id'] for x in report['sources']})


def test_advice_request_does_not_fill_pending_slot(client):
    key, s = start(client, '我睡不着')
    assert s['last_slot'] == 'duration'
    r = client.post(f'/api/sessions/{key}/messages', json={'text': '先给我详细建议'})
    s = r.json()
    assert s['last_slot'] == 'duration'
    assert not s['profile']['duration']
    assert '二、这些表现可能与什么有关' in s['messages'][-1]['content']
    s = client.post(f'/api/sessions/{key}/messages', json={'text': '三天'}).json()
    assert s['profile']['duration'] == '三天'
    assert s['last_slot'] == 'frequency'


def test_profile_edit_changes_report_and_export(client):
    key, s = start(client, '我睡不着')
    profile = dict(s['profile'], complaint='我反酸胃胀', duration='两天')
    s = client.patch(f'/api/sessions/{key}/profile', json=profile).json()
    assert s['advice']['topics'] == ['digestive']
    assert '固定的起床时间' not in s['advice']['text']
    exported = client.get(f'/api/sessions/{key}/export').text
    assert '临睡前进食' in exported
    assert '持续时间：两天' in exported
    assert 'https://www.nhs.uk/conditions/indigestion/' in exported


def test_unknown_and_negated_symptoms_do_not_invent_an_explanation(client):
    _, s = start(client, '我耳鸣，但没有头痛，也没有乏力')
    assert not s['advice']['topics']
    assert '没有匹配到' in s['advice']['text']
    assert '固定的起床时间' not in s['advice']['text']


@pytest.mark.parametrize('text', ['我突然出现剧烈头痛', '我咳嗽并且咳血'])
def test_urgent_symptom_has_no_routine_plan(client, text):
    _, s = start(client, text)
    assert s['messages'][-1]['mode'] == 'risk_notice'
    assert s['stage'] == 'attention'
    assert '优先就医' in s['advice']['text']
    assert '可以先做' not in s['advice']['text']


def test_child_description_does_not_apply_adult_plan(client):
    _, s = start(client, '我孩子最近咳嗽')
    assert '不直接套用成人' in s['advice']['text']
