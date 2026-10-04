'use strict';
const $ = selector => document.querySelector(selector);
let status, selected = null, currentDetail = null, filters = {repo:'',kind:'',state:'',labels:[]};
let draftTimer, draftDirty = false, draftSaving = Promise.resolve(), requestSequence = 0, detailSequence = 0;
let sending = false, pendingSend = null, syncBusy = false;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function notice(text) { $('#notice').textContent = text; $('#notice').hidden = !text; }
function date(value) { return value ? new Date(value).toLocaleString('ja-JP') : '未同期'; }
async function api(path, body) {
  const options = body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-Hub-Request':'1'}, body:JSON.stringify(body)};
  let response;
  try { response = await fetch(path, options); } catch { throw new Error('アプリとの接続が切れました。送信中の場合は、再投稿する前に更新して結果を確認してください。'); }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '処理に失敗しました。');
  return result;
}
async function refreshStatus() {
  status = await api('/api/status');
  $('#repositories').replaceChildren();
  for (const repo of status.repositories) {
    const card = el('div','repo-card');
    card.append(el('div','repo-name',repo.name),el('div','repo-date',`最終更新: ${date(repo.synced_at)}`));
    if (repo.sync_error) card.append(el('p','repo-error',`前回のデータを表示中 · ${repo.sync_error}`));
    const button = el('button','secondary','更新'); button.disabled = syncBusy;
    button.addEventListener('click', () => run(() => syncRepos([repo.name])));
    card.append(button); $('#repositories').append(card);
  }
  const repoSelect = $('#filter-repo'); repoSelect.replaceChildren(new Option('すべて',''));
  status.repositories.forEach(repo => repoSelect.add(new Option(repo.name,repo.name)));
  repoSelect.value = filters.repo;
  $('#backup-status').textContent = status.backup_error || `最新バックアップ: ${date(status.backup_at)}`;
  $('#backup-status').className = status.backup_error ? 'warning' : '';
  $('#auth-status').textContent = status.can_post ? 'GitHubトークン設定済み' : '読み取りモード · 投稿にはGITHUB_TOKENが必要です';
  $('#sync-all').disabled = syncBusy || !status.repositories.length;
}
async function loadItems() {
  const sequence = ++requestSequence;
  const query = new URLSearchParams({repo:filters.repo,kind:filters.kind,state:filters.state});
  filters.labels.forEach(label => query.append('label',label));
  const result = await api('/api/items?' + query);
  if (sequence !== requestSequence) return;
  $('#items').replaceChildren(); $('#item-count').textContent = `${result.items.length}件`;
  $('#label-count').textContent = filters.labels.length ? `(${filters.labels.length})` : '';
  $('#labels').replaceChildren();
  const allLabels = [...new Set([...result.labels,...filters.labels])].sort();
  for (const label of allLabels) {
    const wrapper = el('label'); const input = document.createElement('input'); input.type='checkbox'; input.checked=filters.labels.includes(label);
    input.addEventListener('change', () => { filters.labels = input.checked ? [...filters.labels,label] : filters.labels.filter(value=>value!==label); run(saveFilters); });
    wrapper.append(input,document.createTextNode(label)); $('#labels').append(wrapper);
  }
  if (!result.items.length) { const empty=el('div','empty'); empty.append(el('h2','',status.repositories.length?'一致するIssue・PRはありません':'リポジトリを追加しましょう'),el('p','','更新、または絞り込み条件を変更してください。')); $('#items').append(empty); }
  for (const item of result.items) {
    const active = selected && selected.repo===item.repo && selected.number===item.number;
    const button = el('button',`item${active?' selected':''}`); button.setAttribute('aria-pressed',String(!!active));
    const meta=el('div','item-meta'); meta.append(el('span','kind',item.kind==='pr'?'PR':'ISSUE'),el('span','',`${item.repo} #${item.number}`));
    if(item.has_draft) meta.append(el('span','draft-badge','下書き'));
    const badges=el('div','badges'); badges.append(el('span',`badge state-${item.state}`,item.state));
    item.labels.forEach(label=>badges.append(el('span','badge',label)));
    button.append(meta,el('div','item-title',item.title),badges,el('div','repo-date',date(item.updated_at)));
    if(item.sync_error) button.append(el('div','repo-error','同期失敗 · 前回のデータ'));
    button.addEventListener('click',()=>run(async()=>{if(sending)return;await flushDraft();selected={repo:item.repo,number:item.number};await showDetail();await loadItems();}));
    $('#items').append(button);
  }
}
async function saveFilters() { await api('/api/filters', {...filters, labels:[...filters.labels]}); await loadItems(); await refreshStatus(); }
async function syncRepos(repos) {
  if(syncBusy) return;
  syncBusy=true; await refreshStatus(); const errors=[];
  try {
    for(const repo of repos) { notice(`${repo} を更新しています…`); try{await api('/api/sync',{repo});}catch(error){errors.push(`${repo}: ${error.message}`);} }
    if(selected && !sending) {await flushDraft();await showDetail();}
    await loadItems();
  } finally {syncBusy=false;await refreshStatus();notice(errors.length?errors.join('\n'):'更新しました。');}
}
async function flushDraft() {
  clearTimeout(draftTimer);
  await draftSaving;
  if(!draftDirty || !selected) return;
  const target={...selected}; const body=$('#draft')?.value;
  if(body===undefined) return;
  draftDirty=false;
  const saving=api('/api/draft',{...target,body});draftSaving=saving.catch(()=>{});
  try {const result=await saving; if(selected?.repo===target.repo && selected?.number===target.number && $('#draft')?.value===body) $('#draft-state').textContent=`保存済み ${date(result.saved_at)}`; await refreshStatus();}
  catch(error){draftDirty=true;if($('#draft-state'))$('#draft-state').textContent='保存に失敗 · 再度保存してください';throw error;}
}
async function showDetail() {
  const sequence=++detailSequence, target={...selected};
  const detail=await api('/api/item?'+new URLSearchParams(target));
  if(sequence!==detailSequence) return;
  currentDetail=detail;draftDirty=false;$('#detail').replaceChildren();
  const top=el('div','detail-top'); const meta=el('div','badges');meta.append(el('span','kind',detail.kind==='pr'?'PR':'ISSUE'),el('span',`badge state-${detail.state}`,detail.state));
  const link=el('a','',`${detail.repo} #${detail.number} ↗`);link.href=`https://github.com/${detail.repo}/${detail.kind==='pr'?'pull':'issues'}/${detail.number}`;link.target='_blank';link.rel='noopener noreferrer';top.append(meta,link);
  $('#detail').append(top,el('h2','',detail.title),el('p','muted',`${detail.author} · ${date(detail.updated_at)}`));
  const badges=el('div','badges'); detail.labels.forEach(label=>badges.append(el('span','badge',label)));$('#detail').append(badges,el('div','text-body',detail.body||'本文はありません。'),el('div','section-title',`コメント · ${detail.comments.length}`));
  if(!detail.comments.length)$('#detail').append(el('p','muted','コメントはまだありません。'));
  detail.comments.forEach(comment=>{const card=el('div','comment');card.append(el('div','comment-header',`${comment.author} · ${date(comment.created_at)}`),el('div','text-body',comment.body));$('#detail').append(card);});
  const composer=el('div','composer');const label=el('label','','コメントの下書き');label.htmlFor='draft';const textarea=el('textarea');textarea.id='draft';textarea.value=detail.draft;textarea.placeholder='Markdownでコメントを書く…';
  textarea.addEventListener('input',()=>{draftDirty=true;$('#draft-state').textContent='未保存';clearTimeout(draftTimer);draftTimer=setTimeout(()=>run(flushDraft),800);});
  const actions=el('div','composer-actions'); const saved=el('small','','下書きはローカルDBに保存されます');saved.id='draft-state'; const buttons=el('div'); const save=el('button','secondary','下書きを保存');save.addEventListener('click',()=>run(flushDraft)); const send=el('button','','投稿内容を確認');send.id='open-send';send.disabled=!status.can_post || detail.submissions.some(s=>['unknown','sending'].includes(s.status));send.addEventListener('click',()=>run(prepareSend));buttons.append(save,document.createTextNode(' '),send);actions.append(saved,buttons);composer.append(label,textarea,actions);
  detail.submissions.forEach(attempt=>{
    const names={sent:'投稿済み',failed:'投稿失敗',unknown:'送信結果不明',sending:'送信中',acknowledged:'確認済み'};
    const row=el('div','attempt',`${names[attempt.status]||attempt.status} · ${date(attempt.created_at)}${attempt.error?' · '+attempt.error:''}`);
    if(attempt.comment_id){const resultLink=el('a','',' GitHubで確認 ↗');resultLink.href=link.href+'#issuecomment-'+attempt.comment_id;resultLink.target='_blank';resultLink.rel='noopener noreferrer';row.append(resultLink);}
    if(attempt.status==='unknown'){
      row.append(el('p','warning','まず更新して結果を照合してください。見つからない場合はGitHubで投稿の有無を確認してください。'));
      const ack=el('button','secondary','GitHubで確認済み：再投稿を許可');ack.addEventListener('click',()=>run(async()=>{if(!confirm('GitHubで投稿結果を確認しましたか？確認せず再投稿すると二重投稿になる可能性があります。'))return;await api('/api/acknowledge',{id:attempt.id});await flushDraft();await showDetail();}));row.append(ack);
    }
    composer.append(row);
  });$('#detail').append(composer);
}
async function prepareSend() {
  await flushDraft(); const body=$('#draft').value;
  if(!body.trim())throw new Error('コメント本文を入力してください。');
  pendingSend={...selected,body,id:crypto.randomUUID()};
  $('#confirm-target').textContent=`${selected.repo} #${selected.number} · ${currentDetail.title}`;
  $('#confirm-body').textContent=body;$('#send-dialog').showModal();
}
async function submit() {
  if(sending || !pendingSend) return;
  sending=true;$('#confirm-send').disabled=true;$('#cancel-send').disabled=true;$('#draft').disabled=true;
  try {
    const result=await api('/api/submit',pendingSend);
    $('#send-dialog').close();
    notice(result.status==='sent'?'GitHubに投稿しました。':result.status==='unknown'?'送信結果が不明です。再投稿せず、更新またはGitHubで確認してください。':`投稿できませんでした。${result.error||''}`);
    pendingSend=null;await refreshStatus();await showDetail();await loadItems();
  } catch(error) {
    $('#send-dialog').close();notice(error.message);
    // Re-read durable submission state before allowing any new confirmation.
    try{await showDetail();}catch{if($('#open-send'))$('#open-send').disabled=true;}
  } finally {sending=false;$('#confirm-send').disabled=false;$('#cancel-send').disabled=false;if($('#draft'))$('#draft').disabled=false;}
}
async function run(action) {try{await action();}catch(error){notice(error.message);}}
$('#repo-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>{await api('/api/repositories',{repo:$('#new-repo').value.trim()});$('#new-repo').value='';await refreshStatus();await loadItems();notice('登録しました。「更新」でGitHubのデータを取得できます。');});});
$('#sync-all').addEventListener('click',()=>run(()=>syncRepos(status.repositories.map(repo=>repo.name))));
$('#backup-retry').addEventListener('click',()=>run(async()=>{await api('/api/backup',{});await refreshStatus();notice(status.backup_error||'バックアップを更新しました。');}));
for(const key of ['repo','kind','state'])$('#filter-'+key).addEventListener('change',()=>{filters[key]=$('#filter-'+key).value;run(saveFilters);});
$('#clear-filters').addEventListener('click',()=>run(async()=>{filters={repo:'',kind:'',state:'',labels:[]};for(const key of ['repo','kind','state'])$('#filter-'+key).value='';await saveFilters();}));
$('#cancel-send').addEventListener('click',()=>$('#send-dialog').close());
$('#send-dialog').addEventListener('cancel',event=>{if(sending)event.preventDefault();});
$('#confirm-send').addEventListener('click',()=>run(submit));
window.addEventListener('beforeunload',event=>{if(draftDirty||sending){event.preventDefault();event.returnValue='';}});
run(async()=>{await refreshStatus();filters=status.filters;for(const key of ['repo','kind','state'])$('#filter-'+key).value=filters[key];await loadItems();});
