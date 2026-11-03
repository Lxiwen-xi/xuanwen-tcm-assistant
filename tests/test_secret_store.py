import json
import os
from pathlib import Path
import runpy
import warnings
import httpx
import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from backend import llm, secret_store as vault
from backend.main import create_app

SYNTHETIC = 'synthetic-dpapi-local-test-secret-8274'


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for key in ('DEEPSEEK_API_KEY','DEEPSEEK_KEY_STORAGE','LLM_PROVIDER','ENABLE_LLM',
                'DEEPSEEK_MODEL','DEEPSEEK_BASE_URL'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(llm,'ROOT',tmp_path)
    monkeypatch.setattr(vault,'secret_path',lambda:tmp_path/'user-private'/'key.dpapi')
    return tmp_path/'.env'


@pytest.mark.skipif(os.name!='nt', reason='Real Windows DPAPI round-trip')
def test_real_dpapi_round_trip_and_randomized_ciphertext():
    a,b=vault.encrypt_key(SYNTHETIC),vault.encrypt_key(SYNTHETIC)
    assert a!=b and SYNTHETIC.encode() not in a
    assert vault.decrypt_key(a)==SYNTHETIC and vault.decrypt_key(b)==SYNTHETIC


@pytest.mark.skipif(os.name!='nt', reason='Real Windows DPAPI tamper test')
def test_corrupted_ciphertext_and_wrong_context_cannot_be_used(monkeypatch):
    blob=vault.encrypt_key(SYNTHETIC)
    broken=bytearray(blob);broken[len(vault.MAGIC)+32]^=0xff
    with pytest.raises(vault.SecretStoreError) as exc:vault.decrypt_key(bytes(broken))
    assert SYNTHETIC not in str(exc.value)
    monkeypatch.setattr(vault,'ENTROPY',b'wrong-application-context')
    with pytest.raises(vault.SecretStoreError):vault.decrypt_key(blob)


@pytest.mark.parametrize('key',['','too-short','secret with spaces 12345',None])
def test_invalid_input_does_not_change_configuration(isolated,key):
    isolated.write_text('DEEPSEEK_API_KEY=old-test-value\n',encoding='utf-8')
    with pytest.raises(vault.SecretStoreError):vault.configure_encrypted_key(key,isolated)
    assert isolated.read_text()=='DEEPSEEK_API_KEY=old-test-value\n'
    assert not vault.secret_path().exists()


@pytest.mark.skipif(os.name!='nt', reason='Real Windows DPAPI migration')
def test_migration_removes_plaintext_duplicates_and_preserves_other_settings(isolated):
    isolated.write_text(f'# an old comment {SYNTHETIC}\nexport DEEPSEEK_API_KEY="{SYNTHETIC}"\n'
                        f'DEEPSEEK_API_KEY=old-test-value\nLLM_TIMEOUT_SECONDS=17\n'
                        'DEEPSEEK_MODEL=deepseek-flash\n',encoding='utf-8')
    path=vault.configure_encrypted_key(SYNTHETIC,isolated)
    values=dotenv_values(isolated)
    assert values['DEEPSEEK_API_KEY']=='' and values['DEEPSEEK_KEY_STORAGE']=='windows_dpapi'
    assert values['LLM_TIMEOUT_SECONDS']=='17'
    assert values['ENABLE_LLM']=='true' and values['LLM_PROVIDER']=='deepseek'
    assert SYNTHETIC not in isolated.read_text() and SYNTHETIC.encode() not in path.read_bytes()
    assert 'old-test-value' not in isolated.read_text()
    assert isolated.read_text().count('DEEPSEEK_API_KEY=')==1
    assert not list(isolated.parent.glob('.env.*'))
    assert vault.read_deepseek_key()==SYNTHETIC
    assert llm.config()['key_source']=='windows_dpapi' and llm.config()['key']==SYNTHETIC


def test_encrypt_failure_does_not_clear_existing_env(isolated,monkeypatch):
    isolated.write_text('DEEPSEEK_API_KEY='+SYNTHETIC+'\n',encoding='utf-8')
    def fail(*a,**kw):raise vault.SecretStoreError('Windows 密钥加密失败，原配置未清除。')
    monkeypatch.setattr(vault,'encrypt_key',fail)
    with pytest.raises(vault.SecretStoreError):vault.configure_encrypted_key(SYNTHETIC,isolated)
    assert dotenv_values(isolated)['DEEPSEEK_API_KEY']==SYNTHETIC


@pytest.mark.skipif(os.name!='nt', reason='Real Windows DPAPI storage failure')
def test_ciphertext_write_failure_does_not_clear_env(isolated,monkeypatch):
    isolated.write_text('DEEPSEEK_API_KEY='+SYNTHETIC+'\n',encoding='utf-8')
    def fail(*a,**kw):raise vault.SecretStoreError('配置文件写入失败。')
    monkeypatch.setattr(vault,'_atomic_write',fail)
    with pytest.raises(vault.SecretStoreError):vault.configure_encrypted_key(SYNTHETIC,isolated)
    assert dotenv_values(isolated)['DEEPSEEK_API_KEY']==SYNTHETIC


def test_missing_encrypted_file_disables_model_instead_of_plaintext_fallback(isolated):
    isolated.write_text('ENABLE_LLM=true\nLLM_PROVIDER=deepseek\nDEEPSEEK_KEY_STORAGE=windows_dpapi\n'
                        'DEEPSEEK_API_KEY=unused-old-plaintext\n',encoding='utf-8')
    c=llm.config()
    assert not c['available'] and c['key']=='' and '未找到' in c['reason']
    assert 'unused-old-plaintext' not in json.dumps(llm.public_config())


def test_explicit_environment_variable_overrides_local_vault(isolated,monkeypatch):
    isolated.write_text('ENABLE_LLM=true\nLLM_PROVIDER=deepseek\nDEEPSEEK_KEY_STORAGE=windows_dpapi\n',encoding='utf-8')
    monkeypatch.setenv('DEEPSEEK_API_KEY',SYNTHETIC)
    assert llm.config()['key']==SYNTHETIC and llm.config()['key_source']=='environment'
    assert llm.enabled()


@pytest.mark.skipif(os.name!='nt', reason='Real encrypted configuration and public API')
def test_encrypted_secret_only_reaches_backend_auth_header(isolated,monkeypatch):
    vault.configure_encrypted_key(SYNTHETIC,isolated)
    seen=[]
    def handler(req):
        seen.append(req.headers['Authorization'])
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'OK'}}]})
    original=httpx.Client
    monkeypatch.setattr(llm.httpx,'Client',lambda **kw:original(**kw,transport=httpx.MockTransport(handler)))
    result=llm.test_connection()
    assert result['ok'] and result['key_source']=='windows_dpapi'
    assert seen==['Bearer '+SYNTHETIC]
    assert SYNTHETIC not in json.dumps(result)
    client=TestClient(create_app(isolated.parent/'test.db'))
    public=client.get('/api/status').text
    assert SYNTHETIC not in public and 'windows_dpapi' in public
    assert client.get('/.env').status_code==404
    assert client.get('/secrets/deepseek-key.dpapi').status_code==404


def test_cli_refuses_visible_input_fallback(monkeypatch,capsys):
    namespace=runpy.run_path(str(Path(__file__).resolve().parents[1]/'设置DeepSeek密钥.py'))
    monkeypatch.setattr('sys.argv',['设置DeepSeek密钥.py'])
    def unsupported(*a,**kw):warnings.warn('Cannot hide input',__import__('getpass').GetPassWarning)
    monkeypatch.setattr('getpass.getpass',unsupported)
    assert namespace['main']()==1
    assert '不支持隐藏输入' in capsys.readouterr().out
