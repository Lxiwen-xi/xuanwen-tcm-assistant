const $ = id => document.getElementById(id);
const tcmLabels = {cold_heat:'寒热与汗出',diet_thirst:'饮食、口味与口渴',stool_urine:'大小便情况',sleep_emotion:'睡眠与情绪'};
const labels = {complaint:'主要不适',duration:'持续时间',frequency:'发生频率',associated:'伴随情况',...tcmLabels,history:'既往病史',medicines:'当前用药及过敏情况',details:'补充信息（部位、诱因、单次时长等）'};
const modes = {online_chat:'DeepSeek 连续对话',online_chat_supplement:'DeepSeek 连续对话 · 含模型补充',chat_api_fallback:'本轮回答未采用',conversation:'对话回复',clarification:'待澄清',online_rag_supplement:'知识库依据 + 模型补充',online_supplement:'模型知识补充 · 未获知识库支持',guided:'信息采集',guided_fallback:'追问回退 · 本地',online_question:'DeepSeek 追问',online_rag_advice:'知识库检索 + DeepSeek 建议',online_advice:'DeepSeek 症状建议',advice_api_fallback:'模型异常 · 本地建议',advice_validation_fallback:'校验回退 · 本地建议',offline_advice:'症状解读与建议 · 离线',offline:'离线原文',online:'模型整理',no_evidence:'资料不足',risk_notice:'优先就医提示',boundary:'功能边界',api_fallback:'接口异常 · 原文回退',validation_fallback:'校验回退 · 原文'};
let current = null, busy = false, toastTimer, modelInfo = null;
function el(tag, text, cls) { const node=document.createElement(tag); if(text!==undefined)node.textContent=text; if(cls)node.className=cls; return node; }
function toast(text) { $('toast').textContent=text; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,4500); }
async function api(path, options={}) { const response=await fetch('/api'+path,options); if(!response.ok){let detail;try{detail=(await response.json()).detail;}catch{}throw new Error(typeof detail==='string'?detail:'请求失败，请检查内容或服务状态。');} return response.json(); }
const json = (method, body) => ({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
function button(text, action, cls='') { const b=el('button',text,cls); b.type='button';b.onclick=action;return b; }
async function refreshModel(test=false){const b=$('test-model');b.disabled=true;try{const status=await api('/status');modelInfo=status.model;if(!current&&status.model_available)$('model-consent').checked=true;$('model-consent').disabled=!status.model_available;$('model-status').textContent=modelInfo.note;if(!current)$('mode-badge').textContent=status.model_available&&$('model-consent').checked?'DeepSeek · 连续对话就绪':'离线本地模式';if(test&&status.model_available){$('model-status').textContent='正在连接 '+modelInfo.provider_label+'…';const result=await api('/model/test',{method:'POST'});$('model-status').textContent=result.message+' · '+result.model;toast(result.message);}else if(test){toast(modelInfo.note);}}finally{b.disabled=false;}}
async function act(fn) { try { await fn(); } catch(e) { toast(e.message || '操作失败，请重试。'); } }
function switchTab(tab) { document.querySelectorAll('.tab').forEach(n=>n.classList.toggle('active',n.id==='tab-'+tab));document.querySelectorAll('.nav').forEach(n=>n.classList.toggle('active',n.dataset.tab===tab));$('page-label').textContent={chat:'问诊工作台',history:'历史记录',knowledge:'知识库'}[tab]; if(tab==='history')act(loadHistory); if(tab==='knowledge')act(loadDocuments); }
function showDialog(title, text) { $('dialog-title').textContent=title;$('dialog-content').textContent=text;$('detail-dialog').showModal(); }
function sourceNode(s) { const detail=el('details',undefined,'source');detail.append(el('summary',`[${s.id}] ${s.title}${s.page?' · 第'+s.page+'页':''}`));if(s.section)detail.append(el('small','章节上下文：'+s.section));detail.append(el('p',s.text));detail.append(el('small',s.version+(s.score==null?' · 建议整理依据':' · 检索分数 '+s.score+'（不代表医学置信度）')));if(/^https?:\/\//i.test(s.url)){const a=el('a','查看来源页面 ↗');a.href=s.url;a.target='_blank';a.rel='noopener noreferrer';detail.append(el('br'),a);}return detail; }
function render() {
  if(!current)return;$('model-consent').checked=current.model_consent;
  const area=$('messages');area.replaceChildren();
  if(!current.messages.length){area.append(welcome.cloneNode(true));bindExamples();}
  for(const m of current.messages){const item=el('div',undefined,'message '+m.role);const meta=el('div',undefined,'message-meta');meta.append(el('span',m.role==='user'?'你':'铉问'));if(m.mode)meta.append(el('span',modes[m.mode]||m.mode,'mode-label'));item.append(meta,el('div',m.content,'bubble'));for(const s of m.sources||[])item.append(sourceNode(s));area.append(item);}
  area.scrollTop=area.lastElementChild ? area.lastElementChild.offsetTop-12 : 0;
  for(const key of Object.keys(labels))$('field-'+key).value=current.profile[key]||'';
  $('session-state').textContent={new:'尚未开始',collecting:'正在补充信息',summarized:'信息已整理',attention:'常规追问已停止'}[current.stage]||'进行中';
  $('risk-notice').hidden=!current.risk_notice;$('risk-notice').textContent=current.risk_notice;
  $('save-profile').disabled=false;$('preview-summary').disabled=false;
  $('view-advice').disabled=!current.advice;
  const retrieval=current.advice?.retrieval;$('rag-status').hidden=!retrieval||retrieval.status==='not_run';
  $('rag-status').textContent=retrieval?.status==='matched'?`本轮检索到 ${retrieval.hits} 段资料（上传资料 ${retrieval.external_hits} 段），仅引用与问题相关的内容。`:(current.model_consent?'本轮知识库无相关片段，允许 DeepSeek 补充一般知识。':'知识库暂未命中可用片段，本次回答会说明依据不足。');
  $('export-summary').classList.remove('disabled');$('export-summary').href='/api/sessions/'+current.id+'/export';
  const last=current.messages.filter(m=>m.role==='assistant').at(-1)?.mode||'';
  const modelLabel=current.model_consent?((modelInfo?.provider_label||'模型')+(last.includes('fallback')?' · 本轮回退':last.startsWith('online')?' · 本轮已调用':' · 已授权')):'离线本地模式';
  $('mode-badge').textContent=modelLabel+' · '+(current.save?'本机保留':'临时会话');
  $('model-notice').hidden=!current.model_error;$('model-notice').textContent=current.model_error||'';
  localStorage.setItem('qiwen-session-id',current.id);
}
const welcome=$('welcome').cloneNode(true);
function bindExamples(){document.querySelectorAll('[data-example]').forEach(b=>b.onclick=()=>{$('message-input').value=b.dataset.example;$('message-input').focus();});}
async function newSession(){current=await api('/sessions',json('POST',{save:$('save-session').checked,model_consent:$('model-consent').checked}));switchTab('chat');render();$('message-input').value='';$('message-input').focus();}
async function send(){const text=$('message-input').value.trim();if(!text||busy)return;busy=true;$('send-button').disabled=true;$('send-button').textContent='整理中…';$('message-input').disabled=true;try{if(!current)await newSession();current=await api('/sessions/'+current.id+'/messages',json('POST',{text}));$('message-input').value='';render();}catch(e){toast(e.message);$('message-input').value=text;}finally{busy=false;$('send-button').disabled=false;$('send-button').textContent='发送 ↑';$('message-input').disabled=false;$('message-input').focus();}}
async function loadHistory(){const rows=await api('/sessions');const list=$('history-list');list.replaceChildren();if(!rows.length){list.append(el('div','还没有保留的会话。新建问诊前勾选“保留新会话到本机”即可。','empty'));return;}for(const row of rows){const card=el('article',undefined,'record');card.append(el('h3',row.title),el('p','更新时间：'+new Date(row.updated_at).toLocaleString('zh-CN')));const actions=el('div',undefined,'record-actions');actions.append(button('继续查看',()=>act(async()=>{current=await api('/sessions/'+row.id);switchTab('chat');render();})),button('删除记录',()=>act(async()=>{if(!confirm('删除这份本机记录？'))return;await api('/sessions/'+row.id,{method:'DELETE'});if(current?.id===row.id){current=null;localStorage.removeItem('qiwen-session-id');await newSession();}await loadHistory();toast('记录已删除');}),'danger'));card.append(actions);list.append(card);}}
async function loadDocuments(){const docs=await api('/documents');$('document-count').textContent=docs.length+' 份资料';const list=$('document-list');list.replaceChildren();if(!docs.length)list.append(el('div','暂时没有资料，请从右侧导入。','empty'));for(const d of docs){const card=el('article',undefined,'record');const top=el('div',undefined,'record-top');top.append(el('h3',d.title),el('small',d.active?'已纳入检索':'已停用'));card.append(top,el('p',d.version),el('small',d.builtin?'内置整理稿 · 来源已附链接':'用户导入 · '+d.filename));const actions=el('div',undefined,'record-actions');actions.append(button('查看内容',()=>showDialog(d.title,d.content+'\n\n来源：'+(d.url||'未提供')+'\n版本：'+d.version)),button(d.active?'停用资料':'纳入演示检索',()=>act(async()=>{await api('/documents/'+d.id,json('PATCH',{active:!d.active}));await loadDocuments();toast(d.active?'资料已停用':'已纳入检索；启用不代表经过临床审核');})),button('删除',()=>act(async()=>{if(!confirm('删除资料“'+d.title+'”？'))return;await api('/documents/'+d.id,{method:'DELETE'});await loadDocuments();toast('资料已删除');}),'danger'));card.append(actions);list.append(card);}}
const tcmFields=el('fieldset',undefined,'tcm-fields');tcmFields.append(el('legend','中医问诊信息'),el('p','按当前情况选择性了解，只记录你的描述。','muted'));
for(const [key,label] of Object.entries(labels)){if(key==='cold_heat')$('profile-fields').append(tcmFields);const wrap=el('label',label);const input=el('textarea');input.id='field-'+key;input.name=key;input.maxLength=key==='details'?1500:key==='complaint'?1200:500;input.placeholder='未提供';input.rows=1;wrap.append(input);(key in tcmLabels?tcmFields:$('profile-fields')).append(wrap);}
document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
$('new-session').onclick=()=>act(async()=>{if(busy)return;if(current&&!current.save&&current.messages.length){if(!confirm('当前为临时会话。新建后可使用历史保留的会话；需要的话请先导出当前摘要。继续新建？'))return;}await newSession();});
$('chat-form').onsubmit=e=>{e.preventDefault();send();};
$('message-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send();}};
$('profile-form').onsubmit=e=>{e.preventDefault();act(async()=>{if(!current||busy)return;const profile=Object.fromEntries(Object.keys(labels).map(k=>[k,$('field-'+k).value.trim()]));current=await api('/sessions/'+current.id+'/profile',json('PATCH',profile));render();toast(current.model_consent?'核对已保存；发送“再分析一下”可让模型重新整理':'核对结果已保存');});};
$('preview-summary').onclick=()=>current&&showDialog('问诊信息摘要',current.summary);
$('view-advice').onclick=()=>{if(!current?.advice)return;const a=current.advice;showDialog('症状解读与建议',a.text+'\n\n参考来源\n'+a.sources.map(s=>'['+s.id+'] '+s.title+(s.page?' · 第'+s.page+'页':'')+(s.section?' · '+s.section:'')+'\n'+s.url).join('\n\n'));};
$('close-dialog').onclick=()=>$('detail-dialog').close();
$('detail-dialog').onclick=e=>{if(e.target===$('detail-dialog'))$('detail-dialog').close();};
$('refresh-history').onclick=()=>act(loadHistory);
$('reindex').onclick=()=>act(async()=>{const r=await api('/documents/reindex',{method:'POST'});toast('索引已更新，共 '+r.chunks+' 个资料片段');});
$('upload-form').onsubmit=e=>{e.preventDefault();act(async()=>{const form=$('upload-form'),b=form.querySelector('button');b.disabled=true;try{await api('/documents',{method:'POST',body:new FormData(form)});form.reset();await loadDocuments();toast('资料已导入，默认停用，请核对后启用');}finally{b.disabled=false;}});};
$('save-session').onchange=()=>{if(current)toast('保存设置将在下一次新建问诊时生效');};
$('model-consent').onchange=()=>act(async()=>{if(busy){$('model-consent').checked=!!current?.model_consent;return;}if(current){current=await api('/sessions/'+current.id+'/model',json('PATCH',{model_consent:$('model-consent').checked}));render();toast(current.model_consent?'当前会话已切换到 DeepSeek 连续对话':'当前会话已切换到离线整理');}});
bindExamples();
$('test-model').onclick=()=>act(()=>refreshModel(true));
act(async()=>{await refreshModel();const saved=localStorage.getItem('qiwen-session-id');if(saved){try{current=await api('/sessions/'+saved);render();}catch{localStorage.removeItem('qiwen-session-id');}}});
