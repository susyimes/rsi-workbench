'use strict';
const $ = (selector) => document.querySelector(selector);
const el = (tag, className, text) => { const node = document.createElement(tag); if(className) node.className=className; if(text !== undefined) node.textContent=text; return node; };
let snapshot=null, etag=null, filter='all', detailId=null, polling=false, timer=null, toastTimer=null;
let releasePreview=null;
const time = value => value ? new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value)) : '—';
const stageNames = {analyzing:'分析',developing:'开发',verifying:'验证',releasing:'发布',observing:'反馈'};
const runStatus = {completed:'完成',failed:'失败',expired:'租约超时',cancelled:'已停止',running:'运行中'};

function toast(message) { $('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true,4500); }
async function request(path, body, key, timeout=10000) {
  const headers={'Content-Type':'application/json','X-RSI-Client':'local-dashboard'};
  if(key) headers['Idempotency-Key']=key;
  const response=await fetch(path,{method:'POST',headers,body:JSON.stringify(body),signal:AbortSignal.timeout(timeout)});
  const data=await response.json();
  if(!response.ok) throw new Error(data.error || '请求未成功');
  return data;
}
function render() {
  const real=snapshot.mode==='codex';
  $('#mode-tag').textContent=real?'● 真实 Codex · 独立测试仓库':'○ 演示模式 · 模拟执行器';
  $('#footer-mode').textContent=real?'Python · SQLite · Codex CLI · Kotlin/JVM':'演示模式 · 所有执行结果明确标记为模拟';
  $('#scenario-field').hidden=real;
  const project=$('#create-form [name="project"]'), selected=project.value;
  project.replaceChildren();
  for(const repo of real?(snapshot.repositories||[]):[{id:'demo-site',name:'演示项目',ready:true}]) {
    const option=el('option','',repo.name+(repo.ready?'':' · 待接入验收'));option.value=repo.id;option.disabled=!repo.ready;project.append(option);
  }
  if([...project.options].some(x=>x.value===selected&&!x.disabled)) project.value=selected;
  $('#create-note').textContent=real?'工单使用所选仓库的独立克隆；发布阶段会展示本地测试和 GitHub PR 的具体目标。':'演示模式不调用模型、不修改项目代码。';
  renderRepositories();
  const strip=$('#execution-strip');strip.hidden=!real;strip.replaceChildren();
  if(real) {
    const execution=snapshot.execution||{};
    strip.append(el('span','',`已归档会话 ${execution.codex_calls??'…'}`));
    if(execution.last_checks) strip.append(el('span','',`验收 ${execution.last_checks.passed_count}/${execution.last_checks.total} 通过`));
    strip.append(el('span','','范围：Kotlin 策略样例 · 不含 Android 真机验证'));
    if(execution.release_url && /^http:\/\/127\.0\.0\.1:\d+$/.test(execution.release_url)) {
      const link=el('a','','查看本地发布 ↗');link.href=execution.release_url+'/health';link.target='_blank';link.rel='noopener';strip.append(link);
    }
  }
  const summary=$('#summary'); summary.replaceChildren();
  for(const [key,label] of [['active','进行中'],['pending','待处理'],['done','已完成'],['resident','常驻角色']]) {
    const metric=el('div',`metric ${key}`);metric.append(el('span','metric-label',label),el('strong','metric-value',snapshot.summary[key]));summary.append(metric);
  }
  const roles=$('#roles'); roles.replaceChildren();
  for(const role of snapshot.resident_roles) {
    const row=el('div','role-row'), info=el('div','role-info');
    info.append(el('strong','',role.name),el('p','',role.duty),el('small','role-model',`${role.model||'—'} · ${role.effort||'—'}`));row.append(el('span','role-mark',`◇ ${role.short}`),info,el('span','role-state','常驻'));roles.append(row);
  }
  $('#ticket-total').textContent=snapshot.items.length;
  document.querySelectorAll('.step').forEach(node=>node.classList.remove('is-active'));
  const states=new Set(snapshot.items.map(item=>item.state));
  for(const [state,step] of Object.entries({pending:1,analyzing:2,developing:3,verifying:4,awaiting_release:5,releasing:5,observing:6})) {
    if(states.has(state)) $(`.s${step}`).classList.add('is-active');
  }
  $('.flow-grid').classList.toggle('running',snapshot.summary.active>0);
  $('#loop-caption').textContent=snapshot.summary.active ? `${snapshot.summary.active} 张工单推进中 · ${real?'真实 Codex':'模拟'}` : snapshot.summary.awaiting_release ? '等待版本发布授权' : snapshot.summary.blocked ? `${snapshot.summary.blocked} 张工单阻塞 · 等待处理` : '等待工单启动';
  renderTickets();
  const events=$('#events');events.replaceChildren();
  for(const event of snapshot.timeline.slice(0,15)) {
    const li=el('li');li.append(el('span','event-time',`${time(event.at)}  /  #${String(event.task_id).padStart(3,'0')}`),el('p','',event.message));events.append(li);
  }
  if(!snapshot.timeline.length) events.append(el('li','', '还没有事件。创建工单后，状态变化将记录在这里。'));
}
function actionButton(text, action, id, className='') {
  const button=el('button',className,text);button.type='button';button.dataset.action=action;button.dataset.id=id;return button;
}
function safeLink(label,url,kind='github') {
  const allowed=kind==='local'?/^http:\/\/127\.0\.0\.1:\d+(\/health)?$/:/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+(?:\/pull\/\d+)?$/;
  if(!allowed.test(url||'')) return el('span','','地址不可用');
  const link=el('a','',label);link.href=url;link.target='_blank';link.rel='noopener';return link;
}
function renderRepositories() {
  const target=$('#repository-list');target.replaceChildren();
  const repositories=snapshot.repositories||[];$('#repository-count').textContent=repositories.length;
  for(const repo of repositories) {
    const card=el('article','repository-card');card.append(el('h3','',repo.name),el('code','',repo.path||'内置隔离样例'));
    if(repo.github_repository) card.append(el('p','',`GitHub · ${repo.github_repository}`),safeLink('打开仓库 ↗','https://github.com/'+repo.github_repository));
    else card.append(el('p','release-status','本地仓库 · 尚未发现 GitHub remote'));
    card.append(el('p','',`目标分支 · ${repo.base_branch||'未发现'}`),el('p','',repo.ready?'执行适配器已就绪':'仓库已登记 · 需要接入本项目的验收适配器'));
    const actions=el('div','ticket-actions');
    if(repo.ready) actions.append(actionButton('新建工单','repo-create',repo.id));
    if(repo.path) actions.append(actionButton('重新识别','repo-refresh',repo.id));
    card.append(actions);target.append(card);
  }
}
async function showRelease(id) {
  const response=await fetch(`/api/tasks/${id}/release`,{signal:AbortSignal.timeout(120000)});
  const data=await response.json();if(!response.ok) throw new Error(data.error||'无法读取发布计划');
  releasePreview=data;$('#release-title').textContent=`#${id} 发布详情`;$('#release-error').textContent='';
  const content=$('#release-content');content.replaceChildren();
  content.append(el('p','detail-description',`关联仓库：${data.repository.name}`));
  const local=el('article','release-card');local.append(el('h3','','本地测试服务'));
  if(data.latest_release?.url) local.append(el('p','release-status success','已部署到本机'),safeLink(data.latest_release.url+'/health',data.latest_release.url+'/health','local'));
  else local.append(el('p','release-status','待发布'));
  const pr=el('article','release-card');pr.append(el('h3','','GitHub 草稿 PR'));
  const published=data.publications?.at(-1)||data.latest_release?.pull_request;
  if(published?.status==='published') {
    pr.append(el('p','release-status success',`已创建 · #${published.number} · 发布时为草稿`),safeLink(published.url,published.url),el('p','',`${published.repository} · ${published.head} → ${published.base}`),el('code','',`提交 ${published.head_sha}`),el('p','','创建后进入代码审查 · GitHub CI 状态未查询'));
  } else if(data.plan.pr_available) pr.append(el('p','',`已自动识别：${data.repository.github_repository}`),el('p','',`目标分支：${data.repository.base_branch}`),el('p','release-status','尚未创建 PR'));
  else pr.append(el('p','release-status','未发现 GitHub remote · 尚未发布 PR'),el('p','','请在仓库管理登记带 GitHub remote 的项目，并将新工单关联到该仓库。'));
  content.append(local,pr);
  if(data.task.candidate_digest) content.append(el('div','digest-box',`验证版本 ${data.task.candidate_digest}`));
  $('#release-include-pr').disabled=!data.plan.pr_available;$('#release-include-pr').checked=data.plan.pr_available;
  const eligible=['awaiting_release','done'].includes(data.task.state)&&data.task.candidate_digest&&data.task.verified_digest===data.task.candidate_digest;
  $('#release-confirm').disabled=!eligible;$('#release-confirm').textContent=eligible?'确认发布此验证版本':'通过验证后可发布';
  $('#release-dialog').showModal();
}
function renderTickets() {
  if(!snapshot) return;
  const query=$('#search').value.toLowerCase().trim(), target=$('#tickets');
  const focused=document.activeElement?.dataset;
  const focusKey=focused?.action ? {action:focused.action,id:focused.id} : null;
  target.replaceChildren();
  const items=snapshot.items.filter(item=>(filter==='all'||item.stage===filter)&&(`${item.title} ${item.id} ${item.project}`.toLowerCase().includes(query)));
  for(const item of items) {
    const card=el('article','ticket'), top=el('div','ticket-top');
    top.append(el('span','ticket-id',`WORK / #${String(item.id).padStart(3,'0')}`),el('span','priority',`P${item.priority}`),el('span',`state-tag ${item.stage}`,item.label));
    const bottom=el('div','ticket-bottom'), actions=el('div','ticket-actions');
    if(item.state==='pending') actions.append(actionButton('启动 →','start',item.id,'action-main'));
    if(item.state==='awaiting_release') actions.append(actionButton(snapshot.mode==='codex'?'发布…':'确认模拟发布',snapshot.mode==='codex'?'release':'approve',item.id,'action-main'));
    if(item.state==='blocked') actions.append(actionButton('重新开始','retry',item.id,'action-main'));
    actions.append(actionButton('查看证据','detail',item.id));
    if(snapshot.mode==='codex'&&item.state!=='awaiting_release') actions.append(actionButton('发布详情','release',item.id));
    if(!['done','stopped'].includes(item.state)) actions.append(actionButton('停止','stop',item.id,'danger'));
    bottom.append(el('span','ticket-meta',`${item.repository_name||item.project} · ${item.role} · 修复 ${item.repair_count}/${snapshot.policy.max_repairs}`),actions);
    card.append(top,el('h3','ticket-title',item.title),bottom);target.append(card);
  }
  if(!items.length) target.append(el('div','empty',snapshot.items.length?'没有匹配的工单。试试其他状态或关键词。':'工作台已就绪。创建第一张工单，开始一轮迭代。'));
  if(focusKey) target.querySelector(`[data-action="${focusKey.action}"][data-id="${focusKey.id}"]`)?.focus({preventScroll:true});
}
async function poll(force=false) {
  if(polling || document.hidden && !force) return;
  polling=true;
  try {
    const response=await fetch('/api/snapshot',{headers:etag&&!force?{'If-None-Match':etag}:{},signal:AbortSignal.timeout(10000)});
    if(response.status!==304) {
      if(!response.ok) throw new Error('服务暂时不可用');
      const value=await response.json();
      if(value.schema!==1 || !Array.isArray(value.items) || !value.summary) throw new Error('快照格式无效');
      if(snapshot && value.revision<snapshot.revision) throw new Error('快照版本回退，已保留当前视图');
      const changed=!snapshot||etag!==response.headers.get('ETag');
      snapshot=value;etag=response.headers.get('ETag');
      if(changed) {render();if(detailId && $('#detail-dialog').open) await renderDetail(detailId);}
    }
    $('#connection').classList.remove('error');
    $('#connection').textContent=`● 调度器已连接　·　状态更新 ${time(snapshot?.content_updated_at)} UTC+8　·　每 1.5 秒刷新`;
  } catch(error) {
    $('#connection').classList.add('error');$('#connection').textContent=`连接暂时中断，保留最近状态。${error.message}`;
  } finally { polling=false; }
}
async function renderDetail(id) {
  const response=await fetch(`/api/tasks/${id}`,{signal:AbortSignal.timeout(10000)});
  if(!response.ok) throw new Error('无法读取工单证据');
  const data=await response.json();
  if(detailId!==id) return;
  $('#detail-title').textContent=`#${String(id).padStart(3,'0')} ${data.task.title}`;
  const content=$('#detail-content');const opened=new Set([...content.querySelectorAll('details[open]')].map(x=>x.dataset.run));content.replaceChildren();
  const meta=el('div','detail-meta');meta.append(el('span','',data.task.label),el('span','',`项目 ${data.task.project}`),el('span','',`${snapshot.mode==='codex'?'真实项目':'模拟场景'} ${data.task.scenario}`));content.append(meta);
  content.append(el('p','detail-description',data.task.description||'未填写补充描述。'));
  if(data.task.candidate_digest) content.append(el('div','digest-box',`候选摘要 ${data.task.candidate_digest}\n验证匹配 ${data.task.verified_digest===data.task.candidate_digest?'是':'否'}\n发布确认匹配 ${data.task.approved_digest===data.task.candidate_digest?'是':'否'}`));
  if(!data.runs.length) content.append(el('p','caption','尚未执行。点击工单上的“启动”开始。'));
  for(const run of data.runs) {
    const details=el('details','detail-run');details.dataset.run=run.id;details.open=opened.has(String(run.id));
    details.append(el('summary','',`${stageNames[run.stage]} · ${run.role} · ${runStatus[run.status]} · ${time(run.started_at)}`),el('pre','',JSON.stringify(run.output||{note:'本阶段尚未提交结果'},null,2)));content.append(details);
  }
  if(data.lessons.length) {content.append(el('h3','','经验候选'));for(const lesson of data.lessons) content.append(el('p','detail-description',lesson.content));}
}
$('#tickets').addEventListener('click',async event=>{
  const button=event.target.closest('button[data-action]');if(!button) return;
  const id=Number(button.dataset.id), action=button.dataset.action;button.disabled=true;
  try {
    if(action==='detail') {detailId=id;await renderDetail(id);$('#detail-dialog').showModal();}
    else if(action==='release') await showRelease(id);
    else {await request(`/api/tasks/${id}/${action}`,{});await poll(true);toast({start:'已进入执行队列',approve:snapshot.mode==='codex'?'已授权本地测试发布':'已确认模拟发布',stop:'工单已停止，已有证据保留',retry:'已重新进入执行队列'}[action]);}
  } catch(error) {toast(error.message);} finally {button.disabled=false;}
});
document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));renderTickets();}));
$('#search').addEventListener('input',renderTickets);
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{for(const view of ['console','architecture','repositories']) $('#'+view+'-view').hidden=view!==button.dataset.view;document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b===button));}));
$('#collapse').addEventListener('click',()=>{const body=$('#loop-body');body.hidden=!body.hidden;$('#collapse').setAttribute('aria-expanded',String(!body.hidden));$('#collapse').textContent=body.hidden?'展开 ↓':'收起 ↑';});
$('#create-open').addEventListener('click',()=>{$('#form-error').textContent='';$('#create-dialog').showModal();});
document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>document.getElementById(button.dataset.close).close()));
$('#detail-dialog').addEventListener('close',()=>{detailId=null;});
let submissionKey=null,submissionBody=null;
$('#create-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.currentTarget, button=form.querySelector('[type="submit"]');button.disabled=true;
  const data=Object.fromEntries(new FormData(form));data.priority=Number(data.priority);if(snapshot.mode==='codex') data.scenario='permission_policy';
  const body=JSON.stringify(data);if(body!==submissionBody){submissionKey=crypto.randomUUID();submissionBody=body;}
  try {await request('/api/tasks',data,submissionKey);$('#create-dialog').close();form.reset();submissionKey=null;submissionBody=null;await poll(true);toast('工单已创建，点击“启动”推进流程。');}
  catch(error) {$('#form-error').textContent=error.message;}finally {button.disabled=false;}
});
document.addEventListener('visibilitychange',()=>{if(!document.hidden) poll(true);});
$('#repository-open').addEventListener('click',()=>{$('#repository-error').textContent='';$('#repository-dialog').showModal();});
$('#repository-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.currentTarget,button=form.querySelector('[type="submit"]');button.disabled=true;
  const value=Object.fromEntries(new FormData(form));if(!value.name.trim()) delete value.name;
  try {await request('/api/repositories',value,null,120000);$('#repository-dialog').close();form.reset();await poll(true);toast('仓库已添加，Git 信息已识别');}
  catch(error) {$('#repository-error').textContent=error.message;}finally {button.disabled=false;}
});
$('#repository-list').addEventListener('click',async event=>{
  const button=event.target.closest('button[data-action]');if(!button) return;button.disabled=true;
  try {
    if(button.dataset.action==='repo-create') {$('#create-form [name="project"]').value=button.dataset.id;$('#create-dialog').showModal();}
    else {await request(`/api/repositories/${button.dataset.id}/refresh`,{},null,120000);await poll(true);toast('仓库信息已更新');}
  } catch(error) {toast(error.message);}finally {button.disabled=false;}
});
$('#release-confirm').addEventListener('click',async()=>{
  const button=$('#release-confirm');button.disabled=true;
  try {await request(`/api/tasks/${releasePreview.task.id}/approve`,{include_pr:$('#release-include-pr').checked,plan_digest:releasePreview.plan_digest});$('#release-dialog').close();await poll(true);toast('已按选定目标进入发布阶段');}
  catch(error) {$('#release-error').textContent=error.message;}finally {button.disabled=false;}
});
poll(true);timer=setInterval(()=>poll(),1500);
