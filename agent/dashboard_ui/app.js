const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="spaceqa-token"]').content;
let activeRun = false, repairReady = false, currentRun = null, launching = false, pendingRepairId = null;
let initialized = false, reports = [], selected = null, reportSignature = '', logSignature = '', polling = false;
const el = (tag, cls, value) => { const node = document.createElement(tag); if (cls) node.className = cls; if (value !== undefined) node.textContent = value; return node; };
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-SpaceQA-Token':token},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
function syncActivityMotion() {
  const working = currentRun?.status === 'running';
  const flying = launching || (working && currentRun.kind !== 'repair');
  $('launch-flight').hidden = !flying;
  const hammer = $('repair-motion');
  if (hammer) hammer.hidden = !(pendingRepairId === selected || (working && currentRun.kind === 'repair' && currentRun.report_id === selected));
}
function duration(seconds) { const s = Math.max(0,Math.floor(seconds)); return `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`; }
function showStatus(data) {
  const {run, settings, logs} = data;
  currentRun = run;
  syncActivityMotion();
  if (!initialized) {
    $('game-url').value = settings.game_url; $('max-steps').value = settings.max_steps; $('seed').value = settings.seed;
    initialized = true;
  }
  $('metric-model').textContent = settings.model || 'Not configured';
  const active = run && ['running','stopping'].includes(run.status);
  activeRun = Boolean(active); repairReady = Boolean(settings.configured && settings.repair_configured);
  if ($('fix-bug')) $('fix-bug').disabled = activeRun || !repairReady;
  if ($('apply-fix')) $('apply-fix').disabled = activeRun;
  const stepLines = logs.filter(line => /^INFO Step \d+,/.test(line));
  const steps = stepLines.length ? Number(stepLines.at(-1).match(/Step (\d+)/)[1]) : 0;
  $('metric-status').textContent = run ? run.status[0].toUpperCase()+run.status.slice(1) : 'Idle';
  $('metric-detail').textContent = active ? (run.kind==='repair' ? 'Inspecting code and validating a fix' : 'Exploring your local game') : run?.status === 'failed' ? 'Review the activity log for details' : 'Ready for the next action';
  const repairing = run?.kind === 'repair';
  $('progress-label').textContent = repairing ? 'REPAIR TIME' : 'ACTIONS';
  $('progress-detail').textContent = repairing ? 'Two-minute investigation budget' : 'Actions in the current run';
  const spent = run ? Math.min(run.time_budget_seconds || 120, (run.ended || Date.now()/1000)-run.started) : 0;
  $('metric-steps').replaceChildren(document.createTextNode(repairing ? duration(spent)+' ' : String(steps).padStart(2,'0')+' '),el('em','',repairing ? '/ 02:00' : `/ ${run?.max_steps ?? '—'}`));
  $('run-badge').textContent = run ? run.status.toUpperCase() : 'STANDBY';
  $('run-badge').className = 'badge'+(active ? ' running' : run?.status === 'failed' ? ' failed' : '');
  $('start').disabled = Boolean(active || !settings.configured);
  $('stop').hidden = !active; $('stop').disabled = run?.status === 'stopping';
  for (const id of ['game-url','max-steps','seed','watch']) $(id).disabled = Boolean(active);
  $('run-info').textContent = run ? `${run.kind==='repair'?'REPAIR':'RUN'} ${run.id.toUpperCase()}${run.seed!==null?' · SEED '+run.seed:''}` : 'No active mission';
  $('elapsed').textContent = run ? duration((run.ended || Date.now()/1000)-run.started) : '—';
  $('activity-footer').textContent = active ? 'Agent connected · streaming activity' : run ? `Run ${run.status}${run.exit_code !== null ? ' · exit '+run.exit_code : ''}` : 'Ready when you are';
  $('activity-dot').className = active ? 'dot' : 'dot muted';
  const signature = JSON.stringify(logs);
  if (signature !== logSignature && logs.length) {
    const terminal = $('terminal');
    const atBottom = terminal.scrollHeight-terminal.scrollTop-terminal.clientHeight < 80;
    terminal.replaceChildren(...logs.map(line => el('div','log-line'+(/ERROR|WARNING/.test(line)?' error':/^INFO (Step |Repair: inspecting )/.test(line)?' action':/Outcome:/.test(line)?' outcome':/Gemini review:|suspected bug report/.test(line)?' review':''),line)));
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
  const shouldReload = Boolean(selected && reportSignature && list.some(item=>item.id===selected));
  reportSignature = signature;
  const nodes = list.map(report => {
    const button=el('button','finding'+(selected===report.id?' selected':'')); button.type='button'; button.dataset.report=report.id;
    const top=el('div','finding-top'); top.append(el('span','',report.severity),el('span','finding-status',report.status));
    button.append(top,el('strong','',report.title),el('time','',new Date(report.created).toLocaleString()));
    button.addEventListener('click',()=>loadReport(report.id)); return button;
  });
  $('report-list').replaceChildren(...(nodes.length?nodes:[el('p','list-empty','No findings yet. When Gemini flags unexpected behavior, the evidence will appear here.')]));
  if (shouldReload) loadReport(selected);
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
    const repairBox=el('section','repair-box');
    const repairHeading=el('div','repair-heading');repairHeading.append(el('h4','','Repair agent'));
    const fixButton=el('button','repair-button','Fix bug ↗');fixButton.id='fix-bug';fixButton.type='button';fixButton.disabled=activeRun||!repairReady;
    const repairError=el('p','form-error');repairError.setAttribute('role','alert');
    fixButton.addEventListener('click',async()=>{
      fixButton.disabled=true;repairError.textContent='';$('form-error').textContent='';pendingRepairId=id;syncActivityMotion();
      try {currentRun=await api('/api/repairs/start',{report_id:id});await refresh();}
      catch(error){repairError.textContent=error.message;fixButton.disabled=activeRun||!repairReady;}
      finally{pendingRepairId=null;syncActivityMotion();}
    });
    repairHeading.append(fixButton);repairBox.append(repairHeading);
    const repairMotion=el('div','repair-motion');repairMotion.id='repair-motion';repairMotion.hidden=true;repairMotion.setAttribute('aria-hidden','true');
    repairMotion.append($('repair-animation-template').content.cloneNode(true));repairBox.append(repairMotion);
    const repair=report.repair;
    if(repair){
      repairBox.append(el('div','repair-status',repair.status.replaceAll('_',' ').toUpperCase()),el('p','detail-copy',repair.summary||'Investigating the report…'));
      if(repair.validation)repairBox.append(el('p','repair-validation',repair.validation));
      if(repair.checks?.length){
        const details=el('details','repair-checks');details.append(el('summary','','Validation checks'));
        const output=repair.checks.map(check=>`${check.stage}: exit ${check.exit_code}\n${check.output||''}`).join('\n\n');
        details.append(el('pre','trace',output));repairBox.append(details);
      }
      const repairLinks=el('div','downloads');
      const resultLink=el('a','','Repair details ↗');resultLink.href='/artifacts/repairs/'+encodeURIComponent(repair.id)+'/result.json';resultLink.target='_blank';resultLink.rel='noopener';repairLinks.append(resultLink);
      if(['checks_passed','applied'].includes(repair.status)){
        const patchLink=el('a','','Review patch ↗');patchLink.href='/artifacts/repairs/'+encodeURIComponent(repair.id)+'/fix.patch';patchLink.target='_blank';patchLink.rel='noopener';repairLinks.append(patchLink);
        repairBox.append(el('pre','trace',repair.changes.map(change=>change.path).join('\n')));
      }
      repairBox.append(repairLinks);
      if(repair.status==='checks_passed'){
        const apply=el('button','repair-button','Apply fix to game repo');apply.id='apply-fix';apply.type='button';apply.disabled=activeRun;
        apply.addEventListener('click',async()=>{
          apply.disabled=true;repairError.textContent='';
          try{await api('/api/repairs/apply',{repair_id:repair.id});await loadReport(id);await refresh();}
          catch(error){repairError.textContent=error.message;apply.disabled=activeRun;}
        });repairBox.append(apply);
      }
    }
    repairBox.append(repairError);target.append(repairBox);syncActivityMotion();
    const grid=el('div','evidence-grid');
    const evidence=report.evidence;
    const images=typeof evidence==='object'&&evidence!==null ? [['BEFORE',evidence.before],['AFTER',evidence.after]] : [['FAILURE CAPTURE',evidence||'evidence.png']];
    for(const [label,file] of images) {
      if (!file)continue;
      const fig=el('figure'), link=el('a'); link.href='/artifacts/'+encodeURIComponent(id)+'/'+encodeURIComponent(file);link.target='_blank';link.rel='noopener';
      const img=el('img');img.src=link.href;img.alt=label+' gameplay screenshot';img.loading='lazy';link.append(img);fig.append(el('figcaption','',label),link);grid.append(fig);
    }
    if (report.video?.status === 'ready' && report.video.file) {
      const playback=el('section','video-evidence');
      playback.append(el('h4','','Gameplay recording'));
      const video=el('video');video.controls=true;video.playsInline=true;video.preload='metadata';
      video.setAttribute('aria-label','Original gameplay leading to the suspected bug');
      const toolbar=el('div','video-toolbar');
      const length=el('span','video-duration','Loading recording…');length.setAttribute('role','status');
      const play=el('button','video-play','▶ Play recording');play.type='button';
      const playbackError=el('p','form-error video-error');playbackError.setAttribute('role','alert');
      const showDuration=()=>{length.textContent=Number.isFinite(video.duration)&&video.duration>0 ? `${video.duration.toFixed(1)}s recording` : 'Recording ready · duration unavailable';};
      video.addEventListener('loadedmetadata',showDuration);video.addEventListener('durationchange',showDuration);
      const showState=()=>{play.textContent=video.ended?'↻ Replay':video.paused?'▶ Play recording':'Ⅱ Pause';};
      for(const event of ['play','pause','ended'])video.addEventListener(event,showState);
      video.addEventListener('error',async()=>{
        length.textContent='Playback unavailable';play.disabled=true;
        playbackError.textContent='This browser could not play the recording. Use Open video below to open or download the original file.';
        try{
          const response=await fetch(video.src,{headers:{Range:'bytes=0-0'}});
          await response.body?.cancel();
          if(!video.isConnected)return;
          if(response.status===404)playbackError.textContent='The dashboard server returned HTTP 404 for the video. Restart it with uv run spaceqa-dashboard, then refresh this page.';
          else if(!response.ok)playbackError.textContent=`The video request failed (HTTP ${response.status}). Restart the dashboard and try again.`;
        }catch(error){if(video.isConnected)playbackError.textContent='The video server could not be reached. Start the dashboard again and refresh this page.';}
      });
      play.addEventListener('click',async()=>{
        playbackError.textContent='';
        if(!video.paused){video.pause();return;}
        if(video.ended)video.currentTime=0;
        try{await video.play();}
        catch(error){if(video.isConnected)playbackError.textContent='Playback could not start. Try Open video below to open or download the recording.';}
      });
      toolbar.append(length,play);
      video.src='/artifacts/'+encodeURIComponent(id)+'/'+encodeURIComponent(report.video.file);
      video.poster='/artifacts/'+encodeURIComponent(id)+'/evidence.png';
      playback.append(toolbar,video,playbackError);
      const timed=(report.trace||[]).filter(record=>record.recording_seconds);
      if(timed.length){
        const jump=el('button','text-button jump-to-action','Jump to flagged action ↗');jump.type='button';
        jump.addEventListener('click',()=>{const seek=()=>{video.currentTime=Math.max(0,Math.min(video.duration||Infinity,timed.at(-1).recording_seconds.start-1));video.play().catch(()=>{});};if(video.readyState>=1)seek();else{video.addEventListener('loadedmetadata',seek,{once:true});video.load();}});
        playback.append(jump);
      }
      target.append(playback);
      const snapshots=el('details','screenshot-details');snapshots.append(el('summary','','Before / after snapshots'),grid);target.append(snapshots);
    } else {
      if(report.video?.status==='pending')section(target,'Gameplay recording','Video is being finalized. With --keep-open, press Enter in the agent terminal to finish recording.');
      target.append(grid);
    }
    section(target,'Expected behavior',report.expected_behavior||assessment.expected_behavior);
    section(target,'Actual behavior',report.actual_behavior||assessment.actual_behavior);
    section(target,'Observations',assessment.evidence?.map(item=>'• '+item).join('\n'));
    section(target,'Reasoning & uncertainty',assessment.reasoning);
    section(target,'Suggested follow-up',assessment.follow_up);
    if (report.verification) section(target,'Verification',report.verification.reason || `Replay attempts: ${report.verification.attempts}; verified: ${report.verification.verified}`);
    const links=el('div','downloads');
    for(const [title,file] of [['Open Markdown ↗','bug.md'],['Report JSON ↗','bug.json'],['Replay trace ↗','replay_trace.json']]){const a=el('a','',title);a.href='/artifacts/'+encodeURIComponent(id)+'/'+file;a.target='_blank';a.rel='noopener';links.append(a);}
    if (report.video?.status==='ready' && report.video.file) {const a=el('a','','Open video ↗');a.href='/artifacts/'+encodeURIComponent(id)+'/'+encodeURIComponent(report.video.file);a.target='_blank';a.rel='noopener';links.append(a);}
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
  event.preventDefault();$('form-error').textContent='';$('start').disabled=true;launching=true;syncActivityMotion();
  try {currentRun=await api('/api/runs/start',{game_url:$('game-url').value,max_steps:Number($('max-steps').value),seed:Number($('seed').value),watch:$('watch').checked}); await refresh();}
  catch(error){$('form-error').textContent=error.message;$('start').disabled=false;}
  finally{launching=false;syncActivityMotion();}
});
$('stop').addEventListener('click',async()=>{try{await api('/api/runs/stop',{});await refresh();}catch(error){$('form-error').textContent=error.message;}});
$('refresh').addEventListener('click',refresh);
refresh();setInterval(refresh,1000);
