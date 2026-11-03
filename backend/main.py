"""本机单用户应用。浏览器和 API 同源，不依赖 Node.js。"""
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4
import os
import time
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from .storage import Store
from .knowledge import Retriever, seed, parse_upload, safe_url
from .workflow import build_workflow, summary_text, FIELDS, risk_flags
from .llm import enabled, public_config, test_connection
from .advice import build_advice
from .rag import refresh_context
from .intent import repair_legacy_profile

ROOT = Path(__file__).resolve().parents[1]


class NewSession(BaseModel):
    save: bool = False
    model_consent: bool = False


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    complaint: str = Field(default='', max_length=1200)
    duration: str = Field(default='', max_length=500)
    frequency: str = Field(default='', max_length=500)
    associated: str = Field(default='', max_length=500)
    history: str = Field(default='', max_length=500)
    medicines: str = Field(default='', max_length=500)
    details: str = Field(default='', max_length=1500)
    cold_heat: str = Field(default='', max_length=500)
    diet_thirst: str = Field(default='', max_length=500)
    stool_urine: str = Field(default='', max_length=500)
    sleep_emotion: str = Field(default='', max_length=500)


class Active(BaseModel):
    active: bool


class ModelConsent(BaseModel):
    model_consent: bool


def now():
    return datetime.now(timezone.utc).isoformat()


def create_app(db_path=None):
    app = FastAPI(title='铉问 · 中医智能问诊助手', version='1.0.0')
    store = Store(Path(db_path) if db_path else ROOT / 'data' / 'qiwen.db')
    seed(store)
    retriever = Retriever()
    flow = build_workflow(store, retriever)
    temporary, lock = {}, RLock()
    app.state.store = store

    def get_session(key):
        # 临时会话六小时过期；避免无限积累。
        for sid in list(temporary):
            if time.time() - temporary[sid]['_touched'] > 6 * 3600:
                del temporary[sid]
        session = temporary.get(key) or store.get_session(key)
        if not session:
            raise HTTPException(404, '会话不存在或临时会话已过期。')
        for field in FIELDS:
            session['profile'].setdefault(field,'')
        if repair_legacy_profile(session):
            persist(session)
        return session

    def persist(s):
        s['updated_at'] = now()
        s['_touched'] = time.time()
        if s['save']:
            store.save_session(s)
        else:
            temporary[s['id']] = s

    def public(s):
        refresh_context(store, retriever, s)
        return {**{k: v for k, v in s.items() if not k.startswith('_')},
                'summary': summary_text(s), 'advice': build_advice(s)}

    @app.get('/api/status')
    def status():
        return dict(name='铉问', version='1.0.0', model_available=enabled(),
            model=public_config(),
            documents=len(store.documents(active_only=True)), mode='offline',
            note='风险规则与知识整理稿均未经临床审核；本项目用于本地开发演示。')

    @app.post('/api/model/test')
    def model_test():
        return test_connection()

    @app.post('/api/sessions', status_code=201)
    def new_session(body: NewSession):
        with lock:
            if len(temporary) >= 100:
                oldest = min(temporary, key=lambda x: temporary[x]['_touched'])
                del temporary[oldest]
            s = dict(id=uuid4().hex, save=body.save,
                model_consent=body.model_consent and enabled(), title='新的问诊',
                created_at=now(), updated_at=now(), profile={k: '' for k in FIELDS},
                messages=[], asked=[], last_slot=None, stage='new', last_sources=[], risk_notice='')
            persist(s)
            return public(s)

    @app.get('/api/sessions')
    def sessions():
        return [dict(id=s['id'], title=s['title'], updated_at=s['updated_at'], stage=s['stage'])
            for s in sorted(store.sessions(), key=lambda s: s['updated_at'], reverse=True)]

    @app.get('/api/sessions/{key}')
    def session(key: str):
        with lock:
            return public(get_session(key))

    @app.patch('/api/sessions/{key}/model')
    def set_model(key: str, body: ModelConsent):
        with lock:
            s=get_session(key)
            if body.model_consent and not enabled():
                raise HTTPException(422,public_config()['note'])
            s['model_consent']=body.model_consent
            s['model_error']=''
            persist(s)
            return public(s)

    @app.delete('/api/sessions/{key}')
    def delete_session(key: str):
        with lock:
            get_session(key)
            temporary.pop(key, None)
            store.delete_session(key)
            return {'ok': True}

    @app.post('/api/sessions/{key}/messages')
    def message(key: str, body: Message):
        text = body.text.strip()
        if not text:
            raise HTTPException(422, '请输入内容。')
        with lock:
            s = get_session(key)
            if len(s['messages']) >= 120:
                raise HTTPException(400, '本次会话已达 60 轮，请新建会话。')
            if not s['messages']:
                s['title'] = text[:24]
            s['messages'].append(dict(role='user', content=text, time=now()))
            result = flow.invoke(dict(session=s, text=text))
            s = result['session']
            sources = result.get('sources', [])
            if sources:
                s['last_sources'] = sources
            s['messages'].append(dict(role='assistant', content=result['reply'], sources=sources,
                mode=result['mode'], time=now()))
            persist(s)
            return public(s)

    @app.patch('/api/sessions/{key}/profile')
    def profile(key: str, body: Profile):
        with lock:
            s = get_session(key)
            s['profile'] = body.model_dump()
            s.pop('_model_advice', None)
            s['model_error'] = ''
            flags = risk_flags('。'.join(s['profile'].values()))
            if flags:
                s['stage'] = 'attention'
                s['risk_notice'] = '核对信息触发提示：' + '、'.join(flags) + '。请优先由医务人员评估。'
            persist(s)
            return public(s)

    @app.get('/api/sessions/{key}/export')
    def export(key: str):
        with lock:
            s = get_session(key)
            refresh_context(store, retriever, s)
            return Response(summary_text(s), media_type='text/markdown; charset=utf-8',
                headers={'Content-Disposition': 'attachment; filename="xuanwen-summary.md"'})

    @app.get('/api/documents')
    def documents():
        return [dict(d, active=bool(d['active']), builtin=bool(d['builtin'])) for d in store.documents()]

    @app.post('/api/documents', status_code=201)
    async def upload(file: UploadFile = File(...), title: str = Form(''),
                     url: str = Form(''), version: str = Form('用户导入 · 未经临床审核')):
        raw = await file.read(5 * 1024 * 1024 + 1)
        filename = (file.filename or '').replace('\\', '/').rsplit('/', 1)[-1][:150]
        try:
            content = parse_upload(filename, raw)
        except Exception as e:
            msg = str(e) if isinstance(e, ValueError) else '解析失败，请检查文件格式。'
            raise HTTPException(400, msg)
        doc = dict(id=uuid4().hex, title=(title.strip() or filename)[:120],
            url=safe_url(url.strip())[:1000], version=version[:120], content=content,
            active=0, builtin=0, filename=filename)
        with lock:
            if len(store.documents()) >= 100:
                raise HTTPException(400, '本地演示版最多保存 100 份资料。')
            store.put_document(doc)
        return doc

    @app.patch('/api/documents/{key}')
    def activate(key: str, body: Active):
        with lock:
            if not store.set_active(key, body.active):
                raise HTTPException(404, '资料不存在。')
            return {'ok': True}

    @app.delete('/api/documents/{key}')
    def delete_document(key: str):
        with lock:
            if not store.delete_document(key):
                raise HTTPException(404, '资料不存在。')
            return {'ok': True}

    @app.post('/api/documents/reindex')
    def reindex():
        with lock:
            retriever.signature = None
            retriever.search(store, '问诊')
            return {'chunks': len(retriever.chunks)}

    app.mount('/static', StaticFiles(directory=ROOT / 'frontend'), name='static')

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'frontend' / 'index.html')

    return app


app = create_app()
