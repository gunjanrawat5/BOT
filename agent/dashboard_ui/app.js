const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="spaceqa-token"]').content;
let initialized = false, reports = [], selected = null, reportSignature = '', logSignature = '', polling = false;
const el = (tag, cls, value) => { const node = document.createElement(tag); if (cls) node.className = cls; if (value !== undefined) node.textContent = value; return node; };
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-SpaceQA-Token':token},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
function duration(seconds) { const s = Math.max(0,Math.floor(seconds)); return `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`; }
function showStatus(data) {
  const {run, settings, logs} = data;
  if (!initialized) {
    $('game-url').value = settings.game_url; $('max-steps').value = settings.max_steps; $('seed').value = settings.seed;
    initialized = true;
  }
  $('metric-model').textContent = settings.model || 'Not configured';
  const active = run && ['running','stopping'].includes(run.status);
  const stepLines = logs.filter(line => /^INFO Step \d+,/.test(line));
  const steps = stepLines.length ? Number(stepLines.at(-1).match(/Step (\d+)/)[1]) : 0;
  $('metric-status').textContent = run ? run.status[0].toUpperCase()+run.status.slice(1) : 'Idle';
  $('metric-detail').textContent = active ? 'Exploring your local game' : run?.status === 'failed' ? 'Review the activity log for details' : 'Ready for the next action';
  $('metric-steps').replaceChildren(document.createTextNode(String(steps).padStart(2,'0')+' '),el('em','',`/ ${run?.max_steps ?? '—'}`));
  $('run-badge').textContent = run ? run.status.toUpperCase() : 'STANDBY';
  $('run-badge').className = 'badge'+(active ? ' running' : run?.status === 'failed' ? ' failed' : '');
  $('start').disabled = Boolean(active || !settings.configured);
  $('stop').hidden = !active; $('stop').disabled = run?.status === 'stopping';
  for (const id of ['game-url','max-steps','seed','watch']) $(id).disabled = Boolean(active);
  $('run-info').textContent = run ? `RUN ${run.id.toUpperCase()} · SEED ${run.seed}` : 'No active mission';
  $('elapsed').textContent = run ? duration((run.ended || Date.now()/1000)-run.started) : '—';
  $('activity-footer').textContent = active ? 'Agent connected · streaming activity' : run ? `Run ${run.status}${run.exit_code !== null ? ' · exit '+run.exit_code : ''}` : 'Ready when you are';
  $('activity-dot').className = active ? 'dot' : 'dot muted';
  const signature = JSON.stringify(logs);
  if (signature !== logSignature && logs.length) {
    const terminal = $('terminal');
    const atBottom = terminal.scrollHeight-terminal.scrollTop-terminal.clientHeight < 80;
    terminal.replaceChildren(...logs.map(line => el('div','log-line'+(/ERROR|WARNING/.test(line)?' error':/^INFO Step /.test(line)?' action':/Outcome:/.test(line)?' outcome':/Gemini review:|suspected bug report/.test(line)?' review':''),line)));
    if (atBottom || !logSignature) terminal.scrollTop = terminal.scrollHeight;
    logSignature = signature;
  } else if (!logs.length && logSignature) {
    $('terminal').replaceChildren(el('div','log-line','Starting the agent…')); logSignature='';
  }
}
function showReports(list) {
  reports = list;
  $('metric-reports').textContent = String(list.length).padStart(2,'0');
  $('report-count').textContent = `${list.length} report${list.length===1?'':'s'}`;
  const signature = JSON.stringify(list);
  if (signature === reportSignature) return;
  reportSignature = signature;
  const nodes = list.map(report => {
    const button=el('button','finding'+(selected===report.id?' selected':'')); button.type='button'; button.dataset.report=report.id;
    const top=el('div','finding-top'); top.append(el('span','',report.severity),el('span','finding-status',report.status));
    button.append(top,el('strong','',report.title),el('time','',new Date(report.created).toLocaleString()));
    button.addEventListener('click',()=>loadReport(report.id)); return button;
  });
  $('report-list').replaceChildren(...(nodes.length?nodes:[el('p','list-empty','No findings yet. When Gemini flags unexpected behavior, the evidence will appear here.')]));
}
function section(target,label,value) { if(!value)return; target.append(el('h4','',label),el('p','detail-copy',value)); }
async function loadReport(id) {
  selected=id;
  document.querySelectorAll('.finding').forEach(node=>node.classList.toggle('selected',node.dataset.report===id));
  const target=$('report-detail'); target.replaceChildren(el('p','detail-copy','Loading evidence…'));
  try {
    const report=await api('/api/reports/'+encodeURIComponent(id));
    if (selected!==id)return;
    const assessment=report.assessment||{};
    target.replaceChildren(el('span','detail-status',report.status==='suspected'?'AI SUSPECTED · NOT INDEPENDENTLY VERIFIED':report.status||'unverified'),el('h3','',report.title));
    target.append(el('div','detail-meta',`${report.suspected_severity || 'unknown'} severity · ${report.confidence||'unknown'} confidence · seed ${report.seed}`));
    const grid=el('div','evidence-grid');
    const evidence=report.evidence;
    const images=typeof evidence==='object'&&evidence!==null ? [['BEFORE',evidence.before],['AFTER',evidence.after]] : [['FAILURE CAPTURE',evidence||'evidence.png']];
    for(const [label,file] of images) {
      if (!file)continue;
      const fig=el('figure'), link=el('a'); link.href='/artifacts/'+encodeURIComponent(id)+'/'+encodeURIComponent(file);link.target='_blank';link.rel='noopener';
      const img=el('img');img.src=link.href;img.alt=label+' gameplay screenshot';img.loading='lazy';link.append(img);fig.append(el('figcaption','',label),link);grid.append(fig);
    }
    target.append(grid);
    section(target,'Expected behavior',report.expected_behavior||assessment.expected_behavior);
    section(target,'Actual behavior',report.actual_behavior||assessment.actual_behavior);
    section(target,'Observations',assessment.evidence?.map(item=>'• '+item).join('\n'));
    section(target,'Reasoning & uncertainty',assessment.reasoning);
    section(target,'Suggested follow-up',assessment.follow_up);
    if (report.verification) section(target,'Verification',report.verification.reason || `Replay attempts: ${report.verification.attempts}; verified: ${report.verification.verified}`);
    const links=el('div','downloads');
    for(const [title,file] of [['Open Markdown ↗','bug.md'],['Report JSON ↗','bug.json'],['Replay trace ↗','replay_trace.json']]){const a=el('a','',title);a.href='/artifacts/'+encodeURIComponent(id)+'/'+file;a.target='_blank';a.rel='noopener';links.append(a);}
    if (report.event_timeline) { const a=el('a','','Event timeline ↗'); a.href='/artifacts/'+encodeURIComponent(id)+'/'+encodeURIComponent(report.event_timeline); a.target='_blank'; a.rel='noopener'; links.append(a); }
    target.append(links,el('h4','','Action sequence'),el('pre','trace',JSON.stringify((report.trace||[]).map(item=>item.action),null,2)));
  } catch(error){if(selected===id)target.replaceChildren(el('p','form-error',error.message));}
}
async function refresh() {
  if(polling)return; polling=true;
  try {
    const [status,list]=await Promise.all([api('/api/status'),api('/api/reports')]);
    showStatus(status);showReports(list);
  } catch(error){$('start').disabled=true;$('stop').disabled=true;}
  finally{polling=false;}
}
$('run-form').addEventListener('submit',async event=>{
  event.preventDefault();$('form-error').textContent='';$('start').disabled=true;
  try {await api('/api/runs/start',{game_url:$('game-url').value,max_steps:Number($('max-steps').value),seed:Number($('seed').value),watch:$('watch').checked}); await refresh();}
  catch(error){$('form-error').textContent=error.message;$('start').disabled=false;}
});
$('stop').addEventListener('click',async()=>{try{await api('/api/runs/stop',{});await refresh();}catch(error){$('form-error').textContent=error.message;}});
$('refresh').addEventListener('click',refresh);
refresh();setInterval(refresh,1000);
