'use strict';

const $=id=>document.getElementById(id),text=(id,value)=>$(id).textContent=value??'',clear=id=>$(id).replaceChildren();

const node=(tag,value,cls)=>{const n=document.createElement(tag);n.textContent=value??'';if(cls)n.className=cls;return n;};

let connection={},project=null,selected=new Set(),step=0,groupIndex=0,variationIndex=0,pending=[],busy=false,lastAction=null,assignments=new Map();

let activeJobId=null,watchedJob=null,recoveryJob=null,dismissedRecovery=new Set(),initialized=false;

let discoveryPreview=null,discoveryPreviewBody=null,selectedDiscoveryRun=null,activeDiscoveryRun=null;

function needsRepair(){return project?.pending_generation?.groups?.some(g=>g.variations?.some(v=>['reviewed','needs_repair'].includes(v.review_status)&&!v.validation?.eligible));}

const resumeLabel=()=>needsRepair()?'Repair rejected drafts':'Resume saved draft';

const stamp=s=>`${Math.floor(s/60).toString().padStart(2,'0')}:${(s%60).toFixed(2).padStart(5,'0')}`;

async function api(path,body){const response=await fetch('/api'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Studio-Token':connection.token},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'The request could not be completed.');return data;}

function error(e,action){if(['discovery','resume_discovery'].includes(e.operation)&&e.discoveryRunId&&project){action=()=>job('/projects/'+project.id+'/discovery/'+e.discoveryRunId+'/resume',{});}else if(e.operation==='discovery_spoilers'&&e.discoverySpoilerRunId&&project){action=()=>job('/projects/'+project.id+'/discovery/'+e.discoverySpoilerRunId+'/spoiler-check',{});}text('global-error-message',e.message||e);$('global-error').hidden=false;$('pending-work').hidden=!project?.pending_generation;savedOutputControls();const resumable=e.resumeGeneration&&project?.pending_generation;text('retry',resumable?resumeLabel():'Retry');lastAction=resumable?async()=>{await job('/projects/'+project.id+'/resume',{});go(3);}:action;}

async function attempt(fn){try{$('global-error').hidden=true;await fn();}catch(e){error(e,fn);}}

function setBusy(value,message=''){busy=value;$('activity').hidden=!value;text('activity-message',message);document.querySelectorAll('button,input,select').forEach(n=>{if(n.id==='stop-job')return;if(value){if(!('wasDisabled' in n.dataset))n.dataset.wasDisabled=String(n.disabled);n.disabled=true;}else if('wasDisabled' in n.dataset){n.disabled=n.dataset.wasDisabled==='true';delete n.dataset.wasDisabled;}});$('stop-job').hidden=!value||!activeJobId;if(value)$('stop-job').disabled=false;if(!value){renderNav();if(project)savedOutputControls();}}

async function refreshStatus(){connection=await api('/status');text('connection',connection.configured?'Gemini key connected':'Connect Gemini');text('connection-status',connection.configured?connection.model:'Local studio · No key needed to import');text('budget-status',connection.configured?`Budget used/held $${connection.spent.toFixed(3)} / $${connection.budget.toFixed(2)} · Includes failure reservations`:'Local studio · Import and episode selection use no AI');const picker=$('project-picker');picker.replaceChildren(new Option('Saved projects',''));connection.projects.forEach(p=>picker.add(new Option(p.name,p.id)));if(project){picker.value=project.id;renderDiscovery();}if(initialized)recoverStatus();}

function renderNav(){document.querySelectorAll('[data-step]').forEach(b=>{const n=Number(b.dataset.step);b.disabled=busy||(n>0&&!project)||(n===2&&!selected.size)||(n===3&&!project?.groups?.length);if(n===step)b.setAttribute('aria-current','step');else b.removeAttribute('aria-current');});$('choose-promos').disabled=busy||!selected.size;$('discovery-mode').disabled=busy||!project;$('manual-mode').disabled=busy;$('discovery-mode').setAttribute('aria-pressed',String(step===4||!!project?.discovery_view));$('manual-mode').setAttribute('aria-pressed',String(step!==4&&!project?.discovery_view));}

function go(n){if(busy||(n===2&&!selected.size))return;if(project?.discovery_view&&n!==3){attempt(async()=>{await restoreManualProject();go(n);});return;}step=n;if(n===1&&project)renderEpisodes();if(n===2&&project)renderBrief();if(n===4&&project)renderDiscovery();document.querySelectorAll('.screen').forEach(s=>s.hidden=s.id!=='step-'+n);renderNav();}

async function watchJob(id){

 if(watchedJob===id)return;watchedJob=id;activeJobId=id;setBusy(true,'Recovering job progress…');

 const started=Date.now();

 try{

  let result;

  do{result=await api('/jobs/'+id);text('activity-message',result.message||'Working…');text('activity-detail',`Completed work stays saved. ${Math.floor((Date.now()-started)/1000)}s in this view.`);$('stop-job').hidden=false;$('stop-job').disabled=result.status==='cancelling';$('stop-job').textContent=result.status==='cancelling'?'Stopping…':'Stop generation';if(['queued','running','cancelling'].includes(result.status))await new Promise(r=>setTimeout(r,1200));}while(['queued','running','cancelling'].includes(result.status));

  if(project?.id===result.project_id){const rid=result.run_id||result.discovery_run_id||activeDiscoveryRun||(String(result.operation).includes('discovery')?project.discovery_view:null);project=await api('/projects/'+project.id);if(rid){selectedDiscoveryRun=rid;project=await api('/projects/'+project.id+'/discovery/'+rid+'/view');groupIndex=variationIndex=0;}render();if(rid){step=3;document.querySelectorAll('.screen').forEach(s=>s.hidden=s.id!=='step-3');renderNav();}}

  if(result.status==='error'){const failure=Error(result.error||result.message);failure.operation=result.operation;failure.resumeGeneration=['generate','resume_generation'].includes(result.operation);if(['discovery','resume_discovery'].includes(result.operation))failure.discoveryRunId=result.run_id||result.discovery_run_id||activeDiscoveryRun;if(result.operation==='discovery_spoilers')failure.discoverySpoilerRunId=result.run_id||result.discovery_run_id||activeDiscoveryRun;throw failure;}

  if(['cancelled','interrupted'].includes(result.status))showRecovery(result);

 }finally{activeJobId=null;watchedJob=null;setBusy(false);await refreshStatus();}

}

async function job(path,body){

 if(busy)return;

 if(!connection.configured){$('connect-dialog').showModal();throw Error('Connect Gemini to create drafts or run AI checks.');}

 setBusy(true,'Starting…');

 try{const {job_id,run_id}=await api(path,body);activeDiscoveryRun=run_id||(path.includes('/discovery/')?project.discovery_view:null);if(run_id){selectedDiscoveryRun=run_id;project=await api('/projects/'+project.id+'/discovery/'+run_id+'/view');groupIndex=variationIndex=0;render();}await watchJob(job_id);activeDiscoveryRun=null;}

 catch(e){if(!watchedJob)setBusy(false);await refreshStatus();throw e;}

}

function showRecovery(j){

 if(dismissedRecovery.has(j.id))return;recoveryJob=j;$('recovery-notice').hidden=false;

 text('recovery-message',j.status==='cancelled'?'Generation stopped. Your saved work is safe.':'This job was interrupted when the server stopped.');

}

function recoverStatus(){

 const running=(connection.active_jobs||[])[0];

 if(running&&!watchedJob){if(!project||project.id!==running.project_id){openProject(running.project_id).then(()=>attempt(()=>watchJob(running.id)));}else attempt(()=>watchJob(running.id));}

 const interrupted=(connection.latest_jobs||[]).find(j=>['cancelled','interrupted'].includes(j.status)&&(!project||j.project_id===project.id));

 if(interrupted&&!running)showRecovery(interrupted);

}

$('stop-job').onclick=()=>attempt(async()=>{if(!activeJobId)return;$('stop-job').disabled=true;await api('/jobs/'+activeJobId+'/cancel',{});text('activity-message','Stopping the current request…');text('activity-detail','Saved drafts are kept. A request already received by Gemini may still be billed.');});

$('dismiss-recovery').onclick=()=>{if(recoveryJob)dismissedRecovery.add(recoveryJob.id);$('recovery-notice').hidden=true;};

$('recovery-output').onclick=()=>attempt(async()=>{if(!recoveryJob)return;await openProject(recoveryJob.project_id);if(recoveryJob.run_id){await openDiscoveryRun(recoveryJob.run_id);$('recovery-notice').hidden=true;return;}if(project.pending_generation?.groups?.length)await viewSavedOutput();$('recovery-notice').hidden=true;});



function render(){if(!project)return;$('pending-work').hidden=!project.pending_generation;text('project-name',project.name);$('project-picker').value=project.id;renderEpisodes();renderBrief();renderStudio();renderDiscovery();renderNav();if(busy)setBusy(true,$('activity-message').textContent);}

function renderEpisodes(){clear('episode-list');$('range-from').replaceChildren();$('range-to').replaceChildren();project.episodes.forEach(ep=>{const row=node('label',null,'episode-row'),box=document.createElement('input');box.type='checkbox';box.checked=selected.has(ep.id);box.onchange=()=>{box.checked?selected.add(ep.id):selected.delete(ep.id);counts();};const info=node('div');info.append(node('strong',`Episode ${ep.number}`),node('p',ep.logline||'No episode logline found in this TXT file.'));row.append(box,info);$('episode-list').append(row);$('range-from').add(new Option(`Episode ${ep.number}`,ep.id));$('range-to').add(new Option(`Episode ${ep.number}`,ep.id));});$('range-to').selectedIndex=project.episodes.length-1;counts();}

function counts(){text('selected-count',`${selected.size} selected`);$('select-all').checked=selected.size===project.episodes.length;$('select-all').indeterminate=selected.size>0&&selected.size<project.episodes.length;renderNav();}

function selectedEpisodes(){return project.episodes.filter(ep=>selected.has(ep.id));}

function pools(){const ids=selectedEpisodes().map(ep=>ep.id);if($('promo-mode').value==='single')return [ids];if($('grouping-mode').value==='episode')return ids.map(id=>[id]);const map=new Map();ids.forEach(id=>{const name=(assignments.get(id)||'').trim().toLocaleLowerCase();if(!name)throw Error('Give every episode a promo group name.');if(!map.has(name))map.set(name,[]);map.get(name).push(id);});return [...map.values()];}

function briefSummary(){try{const groups=pools();text('brief-total',`${groups.length} promo${groups.length===1?'':'s'} · One draft each`);text('mode-note',$('promo-mode').value==='single'?'One story from your chosen source pool.':$('grouping-mode').value==='episode'?'Each selected episode supplies its own promo.':'Each named group supplies one promo.');text('brief-error',groups.length>10?'Choose up to 10 promos per run to keep review and cost manageable.':'');$('generate').disabled=busy||groups.length>10||!selected.size;}catch(e){text('brief-error',e.message);$('generate').disabled=true;}}

function renderBrief(){const eps=selectedEpisodes();text('source-count',`${eps.length} selected episode${eps.length===1?'':'s'}`);clear('selected-preview');eps.forEach(ep=>{const row=node('div',null,'selected-episode');row.append(node('strong',`Episode ${ep.number}`),node('p',ep.logline||'No episode logline found in this TXT file.'));$('selected-preview').append(row);});const multiple=$('promo-mode').value==='multiple';$('grouping-field').hidden=!multiple;const custom=multiple&&$('grouping-mode').value==='custom';$('group-assignment').hidden=!custom;clear('assignment-list');eps.forEach((ep,i)=>{if(!assignments.has(ep.id))assignments.set(ep.id,`Promo ${i+1}`);const label=node('label',null,'assignment-row');label.append(node('span',`Episode ${ep.number}`));const input=document.createElement('input');input.value=assignments.get(ep.id);input.maxLength=60;input.setAttribute('aria-label',`Promo group for episode ${ep.number}`);input.oninput=()=>{assignments.set(ep.id,input.value);briefSummary();};label.append(input);$('assignment-list').append(label);});briefSummary();}

function hydrateBrief(){const b=project?.pending_generation?.brief||project?.brief||{};for(const [id,key,fallback]of [['promo-mode','promo_mode','single'],['min-duration','minimum',20],['max-duration','maximum',60],['structure','structure','continuous'],['emphasis','emphasis','']])$(id).value=b[key]??fallback;assignments=new Map();const groups=b.episode_groups||[];groups.forEach((ids,i)=>ids.forEach(id=>assignments.set(id,`Promo ${i+1}`)));$('grouping-mode').value=groups.some(ids=>ids.length>1)?'custom':'episode';$('structure').onchange();}

async function openProject(id){discoveryPreview=null;discoveryPreviewBody=null;selectedDiscoveryRun=null;activeDiscoveryRun=null;project=await api('/projects/'+id);selected=new Set(project.pending_generation?.brief?.selected_ids||project.brief?.selected_ids||[]);groupIndex=variationIndex=0;hydrateBrief();render();go(project.groups.length?3:1);}

function activeVariation(){return project?.groups?.[groupIndex]?.variations?.[0];}



function renderSpoiler(v){const area=$('spoiler-result');area.replaceChildren();const batch=project.spoiler_batch_check;const result=batch?v.spoiler_check:null;$('check-spoilers').textContent=batch?'All promos checked':'Check all promos';if(!result){area.append(node('p','All final promos will be checked together in one optional call.','small'));return;}area.append(node('p',`${batch.count} promos checked together · Findings for this selected promo`,'small'));const label={clear:'No major spoiler detected',warning:'Possible spoilers found',inconclusive:'Needs human review'};area.append(node('strong',label[result.status]||'Needs human review'),node('p',result.summary));(result.findings||[]).forEach(f=>{const item=node('div',null,'spoiler-finding');item.append(node('h3',`${f.severity} · ${f.kind}`),node('p',f.explanation));(f.events||[]).forEach(e=>item.append(node('p',`${e.episode_title||'Episode '+(e.episode_number??'?')} · ${e.speaker} · ${stamp(e.start)}–${stamp(e.end)}\n${e.text}`,'spoiler-evidence')));item.append(node('p','Suggested change: '+f.suggestion));area.append(item);});area.append(node('p',result.limitation,'small'));}



function showEvidence(container,validation){

 container.replaceChildren();(validation?.evidence||[]).forEach(beat=>{

  const section=node('section',null,'story-beat');const heading=node('div',null,'beat-heading');heading.append(node('h3',beat.role),node('p',beat.reason,'beat-reason'));section.append(heading);

  beat.events.forEach(e=>{const ep=project.episodes.find(ep=>ep.events.some(event=>event.id===e.id));const turn=node('div',null,'source-turn');turn.append(node('div',`${ep?'Episode '+ep.number+' · ':''}${e.speaker} · ${stamp(e.start)}–${stamp(e.end)}`,'source-meta'),node('p',e.text,'dialogue'));section.append(turn);});container.append(section);

 });

}

function renderTimeline(v){

 clear('source-timeline');let offset=0;v.segments.forEach((s,i)=>{const ep=project.episodes.find(e=>e.id===s.episode_id),duration=s.end-s.start;const cut=node('div',null,'timeline-cut');cut.style.flexGrow=Math.max(duration,1);cut.append(node('strong',`Cut ${i+1} · Ep ${ep?.number??'?'}`),node('span',`${stamp(s.start)}–${stamp(s.end)}`),node('small',`Promo ${stamp(offset)} → ${stamp(offset+duration)}`));$('source-timeline').append(cut);offset+=duration;});

}





function manualSavedOutputControls(){if(!project)return;text('resume-pending',resumeLabel());text('pending-description',needsRepair()?'Saved drafts failed cut checks. Repair uses their failure reasons to request a corrected cut; approved work is kept. This uses Gemini.':'A saved generation is available. Resume unfinished checks without regenerating completed work.');const count=project.pending_generation?.groups?.reduce((n,g)=>n+(g.variations?.length||0),0)||0;$('view-saved-output').textContent=`View saved output (${count})`;$('view-saved-output').disabled=busy||!count;const readonly=!!project.saved_output_view;$('pending-work').hidden=readonly||!project.pending_generation;$('check-spoilers').hidden=readonly;text('spoiler-help',readonly?'Saved snapshot. Return to project to run a spoiler check; existing findings are shown below.':'One batch call checks all final promos against their assigned episodes; findings follow the selected promo.');const notice=$('saved-output-notice');notice.hidden=!readonly;notice.replaceChildren();if(readonly){const s=project.saved_output_summary;notice.append(node('strong',`${s.saved} saved · ${s.eligible} passed checks · ${s.needs_changes} need changes · ${s.awaiting_review} awaiting review · ${s.not_generated} not generated`),node('p','Saved snapshot · No quota used · Unreviewed drafts are not approved.'));const back=node('button','Return to project','quiet');back.onclick=()=>attempt(()=>openProject(project.id));notice.append(back);const resume=node('button',resumeLabel(),'quiet');resume.disabled=busy;resume.onclick=()=>attempt(async()=>{await openProject(project.id);await job('/projects/'+project.id+'/resume',{});go(3);});notice.append(resume);}document.querySelectorAll('#refine-form input,#refine-form select,#refine-form button,.prompt-presets button,#check-spoilers').forEach(n=>n.disabled=readonly||busy);$('check-spoilers').disabled=readonly||busy||!project.groups.length||!!project.pending_generation;document.querySelector('.refine').hidden=readonly;$('refine-form').hidden=readonly;document.querySelector('.prompt-presets').hidden=readonly;if(readonly&&activeVariation()?.review_status==='pending')text('eligibility','Saved draft · Editorial review pending');}

async function viewSavedOutput(){if(busy||!project)return;project=await api('/projects/'+project.id+'/saved-output');groupIndex=variationIndex=0;render();go(3);}



function checkLabel(c,v){const missing=(c.name==='Complete editorial review'&&!(v.rubric||[]).length)||(c.name==='Standalone story & unresolved ending'&&!Object.keys(v.narrative||{}).length);return c.assessed===false||missing?'Not assessed':c.passed?'Pass':'Fail';}
function renderStudio(){const groups=project.groups||[];const picker=$('group-select');picker.replaceChildren();groups.forEach((g,i)=>picker.add(new Option((g.title||`Story ${i+1}`)+(g.variations?.[0]?.validation?.eligible?' · Passed':' · Needs repair'),i)));if(groupIndex>=groups.length)groupIndex=0;picker.value=groupIndex;const v=activeVariation();$('studio-empty').hidden=!!v;$('variation-panel').hidden=!v;clear('angle-tabs');$('angle-tabs').hidden=true;text('group-opportunity',groups[groupIndex]?.opportunity||'');const suffix=project.saved_output_view?'?saved=true':'';$('export').href='/api/projects/'+project.id+'/export.pdf'+suffix;$('json-export').href='/api/projects/'+project.id+'/export'+suffix;$('handoff-export').href='/api/projects/'+project.id+'/handoff'+suffix;if(!v){$('spoiler-result').replaceChildren();savedOutputControls();return;}groups[groupIndex].variations.slice(0,1).forEach((x,i)=>{const tab=node('button',x.angle||`Variation ${i+1}`);tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(i===variationIndex));tab.onclick=()=>{variationIndex=i;renderStudio();};$('angle-tabs').append(tab);});text('cut-title',v.angle);renderTimeline(v);text('studio-title',`The cut room. ${groupIndex+1} / ${groups.length}`);text('angle-purpose',v.distinction);text('studio-source',v.segments.map(s=>{const ep=project.episodes.find(e=>e.id===s.episode_id);return `${ep?.title||'Unknown episode'} · ${stamp(s.start)}–${stamp(s.end)}`;}).join(' → '));text('cut-duration',`${v.validation.duration}s`);text('cut-mode',project.brief.structure);text('eligibility',v.validation.eligible?'Cut checks passed':'Needs another cut · Failed checks');const weights={'Hook':25,'Standalone clarity':25,'Escalation':15,'Cliffhanger':25,'Cut quality':10};const scores=new Map((v.rubric||[]).map(r=>[r.criterion,r.score]));const rubricScore=v.validation.rubric_score??(v.review_status!=='pending'&&Object.keys(weights).every(k=>Number.isFinite(scores.get(k)))?Math.round(Object.keys(weights).reduce((n,k)=>n+scores.get(k)*weights[k]/5,0)):null);text('potential-score',rubricScore===null?'—':rubricScore+'/100');text('score-state',rubricScore===null?(v.validation.eligible?'Not reviewed · Run Virality Review when ready.':'Not reviewed · Fix cut checks to enable Virality Review.'):(v.validation.eligible?'Review complete · Required checks passed.':'Review complete · Required checks still need repair.'));text('batch-summary',`${groups.length} promos · ${groups.filter(g=>g.variations[0]?.validation?.eligible).length} passed · ${groups.filter(g=>!g.variations[0]?.validation?.eligible).length} need repair`);$('repair-current').hidden=!!v.validation.eligible||!!project.saved_output_view;$('repair-current').disabled=busy; text('unanswered-question',v.question);showEvidence($('story-beats'),v.validation);clear('rubric');$('rubric').hidden=!(v.rubric||[]).length;(v.rubric||[]).forEach(r=>{const row=node('div',null,'rubric-row');row.append(node('strong',`${r.criterion} · ${r.score}/5`),node('p',r.reason));$('rubric').append(row);});clear('checks');v.validation.checks.forEach(c=>{const row=node('p',`${checkLabel(c,v)} · ${c.name}`);const detail=node('details',null,'check-detail');detail.append(node('summary',`${checkLabel(c,v)} · ${c.name}`),node('p',c.detail));$('checks').append(detail);});text('checks-count',`${v.validation.checks.filter(c=>c.passed).length}/${v.validation.checks.length}`);text('editor-note',[v.weakness,v.review_notes].filter(Boolean).join(' '));clear('cut-warnings');v.validation.warnings.forEach(w=>$('cut-warnings').append(node('p',w)));renderSpoiler(v);const proposal=project.proposals[v.id];$('proposal').hidden=!proposal;if(proposal){const area=$('proposal');area.replaceChildren(node('h3','Proposed source cut'),node('p',proposal.change),node('p','Tradeoff: '+proposal.tradeoff),node('p',`${proposal.variation.validation.duration}s · ${proposal.variation.validation.eligible?'Checks passed':'Failed checks — cannot apply'}`));showEvidence(area.appendChild(node('div')),proposal.variation.validation);if(!proposal.variation.validation.eligible){const failures=node('div',null,'proposal-failures');failures.append(node('h3','What still needs changing'));proposal.variation.validation.checks.filter(c=>!c.passed).forEach(c=>failures.append(node('p',c.name+': '+c.detail)));if(proposal.variation.review_notes)failures.append(node('p',proposal.variation.review_notes));area.append(failures);}const apply=node('button','Apply this cut','button primary');apply.disabled=!proposal.variation.validation.eligible;apply.onclick=()=>attempt(async()=>{project=await api('/projects/'+project.id+'/apply',{variation_id:v.id});renderStudio();});const discard=node('button','Keep current','button secondary');discard.onclick=()=>attempt(async()=>{project=await api('/projects/'+project.id+'/discard',{variation_id:v.id});renderStudio();});area.append(apply,discard,node('p','Edit your prompt and suggest again to try another cut.'));}savedOutputControls();}



function filesChosen(files){pending=Array.from(files);if(pending.some(f=>!f.name.toLowerCase().endsWith('.txt'))){text('upload-feedback','Choose TXT files only.');pending=[];}$('pending-files').replaceChildren(...pending.map(f=>node('li',f.name)));$('import-project').disabled=!pending.length;text('upload-feedback',pending.length?`${pending.length} files ready to import`:'Choose timestamped TXT files.');}

$('choose-files').onclick=()=>$('files').click();$('files').onchange=e=>filesChosen(e.target.files);$('dropzone').ondragover=e=>e.preventDefault();$('dropzone').ondrop=e=>{e.preventDefault();filesChosen(e.dataTransfer.files);};

$('upload-form').onsubmit=e=>{e.preventDefault();attempt(async()=>{setBusy(true,'Extracting episode numbers and loglines locally…');try{const files=await Promise.all(pending.map(async f=>({name:f.name,text:await f.text()})));project=await api('/projects',{name:$('upload-name').value,files});selected=new Set();hydrateBrief();render();await refreshStatus();}finally{setBusy(false);}go(1);});};

$('select-all').onchange=e=>{selected=new Set(e.target.checked?project.episodes.map(ep=>ep.id):[]);renderEpisodes();};$('apply-range').onclick=()=>{const a=project.episodes.findIndex(ep=>ep.id===$('range-from').value),b=project.episodes.findIndex(ep=>ep.id===$('range-to').value);selected=new Set(project.episodes.slice(Math.min(a,b),Math.max(a,b)+1).map(ep=>ep.id));renderEpisodes();};$('choose-promos').onclick=()=>go(2);$('change-episodes').onclick=()=>go(1);

$('promo-mode').onchange=renderBrief;$('grouping-mode').onchange=renderBrief;

async function generate(){const episode_groups=pools();await job('/projects/'+project.id+'/generate',{selected_ids:selectedEpisodes().map(ep=>ep.id),promo_mode:$('promo-mode').value,episode_groups,minimum:Number($('min-duration').value),maximum:Number($('max-duration').value),structure:$('structure').value,emphasis:$('emphasis').value});groupIndex=variationIndex=0;renderStudio();go(3);}

$('brief-form').onsubmit=e=>{e.preventDefault();if(!selected.size){text('brief-error','Select at least one episode.');return;}if(project.groups.length)$('replace-dialog').showModal();else attempt(generate);};$('confirm-replace').onclick=()=>{$('replace-dialog').close();attempt(generate);};$('cancel-replace').onclick=()=>$('replace-dialog').close();$('structure').onchange=()=>text('structure-note',$('structure').value==='continuous'?'One uninterrupted source sequence. A multi-episode pool chooses its strongest continuous moment.':'Ordered source segments can span the assigned episodes.');$('group-select').onchange=()=>{groupIndex=Number($('group-select').value);variationIndex=0;renderStudio();};

$('repair-current').onclick=()=>attempt(async()=>{await job('/projects/'+project.id+'/refine',{variation_id:activeVariation().id,part:'Whole promo',prompt:'Repair the failed checks using the reviewer reasons. Follow one causal storyline with enough connecting context. Remove unrelated subplots. Keep the opening conflict and end before its resolution, with complete source dialogue and requested timing. Propose one corrected cut.'});$('proposal').scrollIntoView({behavior:'smooth',block:'start'});});

function updateViralityButton(){const v=activeVariation(),button=$('run-virality-review');button.hidden=!!project?.saved_output_view;button.disabled=busy||!v||!v.validation?.eligible||!!project?.pending_generation;button.textContent=project?.discovery_view?'Review discovery promos':(v?.rubric?.length?'Run Virality Review again':'Run Virality Review');}
$('run-virality-review').onclick=()=>attempt(()=>job('/projects/'+project.id+'/virality-review',project.discovery_view?{run_id:project.discovery_view}:{variation_id:activeVariation().id}));
$('check-spoilers').onclick=()=>attempt(()=>job('/projects/'+project.id+(project.discovery_view?'/discovery/'+project.discovery_view:'')+'/spoiler-check',{}));

$('refine-form').onsubmit=e=>{e.preventDefault();attempt(()=>job('/projects/'+project.id+'/refine',{variation_id:activeVariation().id,prompt:$('prompt').value,part:$('refine-target').value}));};document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{$('prompt').value=b.dataset.prompt;$('prompt').focus();});

$('score-info').onclick=()=>{$('score-explanation').hidden=!$('score-explanation').hidden;$('score-info').setAttribute('aria-expanded',String(!$('score-explanation').hidden));};

document.querySelectorAll('[data-step]').forEach(b=>b.onclick=()=>{const n=Number(b.dataset.step);if(project?.discovery_view)attempt(async()=>{await restoreManualProject();go(n);});else go(n);});$('adjust-brief').onclick=()=>go(project?.discovery_view?4:2);$('connection').onclick=()=>{$('model').value=connection.model||'gemini-3.5-flash-lite';$('connect-dialog').showModal();};$('close-connect').onclick=()=>$('connect-dialog').close();$('connect-form').onsubmit=async e=>{e.preventDefault();$('save-connect').disabled=true;text('connect-error','Checking model access…');try{await api('/connect',{key:$('api-key').value,model:$('model').value,budget:Number($('budget').value)});$('api-key').value='';await refreshStatus();$('connect-dialog').close();text('connect-error','');}catch(e){text('connect-error',e.message);}finally{$('save-connect').disabled=false;}};

$('project-picker').onchange=()=>{if($('project-picker').value)attempt(()=>openProject($('project-picker').value));};$('new-project').onclick=()=>{discoveryPreview=null;discoveryPreviewBody=null;selectedDiscoveryRun=null;activeDiscoveryRun=null;project=null;selected.clear();assignments.clear();go(0);text('project-name','New project');};$('home').onclick=e=>{e.preventDefault();go(0);};$('dismiss-error').onclick=()=>$('global-error').hidden=true;$('retry').onclick=()=>{if(lastAction)attempt(lastAction);};

$('view-saved-output').onclick=()=>attempt(viewSavedOutput);

$('resume-pending').onclick=()=>attempt(async()=>{await job('/projects/'+project.id+'/resume',{});go(3);});

$('new-project').addEventListener('click',()=>{$('pending-work').hidden=true;});

attempt(async()=>{await refreshStatus();const target=new URLSearchParams(location.search).get('project');if(target&&connection.projects.some(p=>p.id===target))await openProject(target);initialized=true;recoverStatus();});

/* Show-wide discovery is an independent source-backed run, never a manual draft replacement. */

function discoveryRun(){return project?.discovery_report||project?.discovery_runs?.find(r=>r.id===selectedDiscoveryRun)||null;}

function runDate(run){const raw=run.created_at||run.created;if(!raw)return 'Saved run';const date=new Date(typeof raw==='number'?raw*1000:raw);return Number.isNaN(date.getTime())?'Saved run':date.toLocaleString();}

function runStatus(run){const job=[...(connection.active_jobs||[]),...(connection.latest_jobs||[])].find(j=>j.run_id===run.id||j.discovery_run_id===run.id);const status=run.status==='done'?'done':job&&['error','interrupted','cancelled'].includes(job.status)?job.status:run.status;const labels={queued:'Queued',running:'Scanning',completed:'Complete',complete:'Complete',done:'Complete',cancelled:'Stopped',interrupted:'Interrupted',error:'Needs attention',pending:'Pending',no_qualifying:'No qualifying cuts'};return labels[status]||String(status||'Saved').replaceAll('_',' ');}

function discoveryCounts(run){const coverage=Array.isArray(run.coverage)?run.coverage:[],scanned=coverage.filter(c=>['scanned','complete','completed','no_candidate','no_candidates','reviewed'].includes(c.status)||c.scanned===true).length;return {total:run.plan?.windows?.length||coverage.length,scanned,shortlist:run.shortlist?.length||0,rejected:run.rejections?.length||0};}

function runCoverageSummary(run){const c=discoveryCounts(run);return `${c.scanned} / ${c.total} source windows scanned · ${Math.max(0,c.total-c.scanned)} pending · ${c.shortlist} shortlisted · ${c.rejected} rejected`;}

async function restoreManualProject(){if(!project?.discovery_view)return;const ids=new Set(selected);project=await api('/projects/'+project.id);selected=ids;groupIndex=variationIndex=0;hydrateBrief();render();}

async function openDiscoveryRun(rid,index=0){if(busy||!project)return;selectedDiscoveryRun=rid;project=await api('/projects/'+project.id+'/discovery/'+encodeURIComponent(rid)+'/view');groupIndex=index;variationIndex=0;render();go(3);}

function savedOutputControls(){manualSavedOutputControls();updateViralityButton();const discovery=!!project?.discovery_view;for(const id of ['export','handoff-export'])$(id).hidden=discovery;if(!discovery){text('adjust-brief','Adjust the brief');text('json-export','Source data');const empty=$('studio-empty');empty.querySelector('h2').textContent='No eligible cuts yet.';empty.querySelector('p').textContent='Choose your episodes and brief to create one draft per promo.';return;}

 const run=discoveryRun()||{},notice=$('saved-output-notice');notice.hidden=false;notice.replaceChildren(node('strong',`${runStatus(run)} · ${runCoverageSummary(run)}`),node('p','Discovery output · Your manual drafts are preserved. Saved cuts can be opened without a key. Refinement is not available for discovery cuts.'));

 const back=node('button','Coverage & run report','quiet');back.onclick=()=>go(4);notice.append(back);const manual=node('button','Return to manual drafts','quiet');manual.onclick=()=>attempt(async()=>{await restoreManualProject();go(project.groups?.length?3:1);});notice.append(manual);

 const group=project.groups?.[groupIndex];if(group){text('group-opportunity',`${group.posting_priority?'Posting priority '+group.posting_priority:'Unranked candidate'} · ${group.posting_reason||group.opportunity||'Run Virality Review to score this discovery.'}`);}

 $('pending-work').hidden=true;document.querySelector('.refine').hidden=true;$('repair-current').hidden=true;$('proposal').hidden=true;$('export').hidden=true;$('handoff-export').hidden=true;$('json-export').href='/api/projects/'+project.id+'/discovery/'+project.discovery_view+'/export';text('json-export','Discovery report · JSON');text('adjust-brief','Review discovery report');

 const empty=$('studio-empty');empty.querySelector('h2').textContent='No qualifying cuts in this run yet.';empty.querySelector('p').textContent='Review coverage and rejection reasons. A stopped or incomplete run can be resumed explicitly from its report.';

 $('check-spoilers').hidden=false;$('check-spoilers').disabled=busy||!run.shortlist?.length||run.status!=='done';text('spoiler-help','Optional: one batch request checks every shortlisted promo. Saved findings follow the selected promo.');

}

function discoveryBody(){const scope=$('discovery-scope').value,ids=scope==='all'?project.episodes.map(e=>e.id):selectedEpisodes().map(e=>e.id);if(!ids.length)throw Error('Select episodes in Episodes, or choose all uploaded episodes.');const minimum=Number($('discovery-min').value),maximum=Number($('discovery-max').value),quantity=$('discovery-quantity').value,limit=quantity==='auto'?30:Number($('discovery-limit').value);if(minimum<20||maximum>60||minimum>maximum)throw Error('Choose a duration between 20 and 60 seconds, with minimum no greater than maximum.');if(!Number.isInteger(limit)||limit<1||limit>30)throw Error('Choose a maximum between 1 and 30 promos.');return {selected_ids:ids,structure:$('discovery-structure').value,quantity,limit,minimum,maximum,emphasis:$('discovery-emphasis').value};}

function invalidateDiscoveryPreview(){discoveryPreview=null;discoveryPreviewBody=null;$('discovery-preview').hidden=true;text('discovery-error','');renderDiscoveryScope();}

function renderDiscoveryScope(){if(!project)return;const all=$('discovery-scope').value==='all',eps=all?project.episodes:selectedEpisodes();text('discovery-scope-summary',`${eps.length} of ${project.episodes.length} uploaded episodes in scope${all?' · Whole uploaded show':eps.length?' · Episodes '+eps.map(e=>e.number).join(', '):' · Choose episodes in Episodes first'}`);$('discovery-limit-field').hidden=$('discovery-quantity').value!=='cap';}

function renderDiscovery(){if(!project)return;renderDiscoveryScope();const area=$('discovery-history');area.replaceChildren();const runs=[...(project.discovery_runs||[])].reverse();if(!runs.length){area.append(node('p','No discovery runs yet. Preview a scan to see the source coverage and estimated cost before starting.','discovery-empty'));$('discovery-run-report').hidden=true;return;}

 if(!selectedDiscoveryRun||!runs.some(r=>r.id===selectedDiscoveryRun))selectedDiscoveryRun=runs[0].id;

 runs.forEach((run,i)=>{const row=node('div',null,'discovery-history-row'),copy=node('div');copy.append(node('strong',`${runStatus(run)} · ${run.shortlist?.length||0} shortlisted`),node('p',`${run.plan?.selected_ids?.length||0} episodes · ${run.plan?.structure||'either'} · ${runDate(run)}`,'small'));const button=node('button',selectedDiscoveryRun===run.id?'Viewing report':'Review report','quiet');button.setAttribute('aria-pressed',String(selectedDiscoveryRun===run.id));button.onclick=()=>{selectedDiscoveryRun=run.id;renderDiscovery();};row.append(copy,button);area.append(row);});

 const run=runs.find(r=>r.id===selectedDiscoveryRun);$('discovery-run-report').hidden=!run;if(run)renderDiscoveryReport(run);

}

function reportDetails(title,rows){const details=node('details',null,'discovery-details');details.append(node('summary',title));const list=node('div');rows.forEach(row=>list.append(node('p',row)));details.append(list);return details;}

function renderDiscoveryReport(run){const body=$('discovery-report-body');body.replaceChildren();const plan=run.plan||{},coverage=Array.isArray(run.coverage)?run.coverage:[];body.append(node('h3',runStatus(run)),node('p',runCoverageSummary(run),'discovery-coverage-summary'),node('p',`${plan.selected_ids?.length||0} episodes · ${plan.minimum??20}–${plan.maximum??60}s · ${plan.structure||'either'} · ${plan.quantity==='cap'?'Up to '+plan.limit:'Recommended shortlist'}`,'small'));if(plan.emphasis)body.append(node('p','Emphasis: '+plan.emphasis,'small'));

 body.append(node('p','Coverage means these source windows were processed. It does not guarantee every possible promo was found. Unscanned or pending work is not a rejection.','small'));

 const actions=node('div',null,'discovery-run-actions'),view=node('button','Open saved shortlist','button secondary');view.disabled=busy||!(run.shortlist?.length);view.onclick=()=>attempt(()=>openDiscoveryRun(run.id));actions.append(view);

 if(!['complete','completed','done','no_qualifying'].includes(run.status)){const resume=node('button','Resume this run','button primary');resume.disabled=busy;resume.onclick=()=>attempt(async()=>{await job('/projects/'+project.id+'/discovery/'+run.id+'/resume',{});});actions.append(resume);}

 const download=node('a','Download run report · JSON','quiet');download.href='/api/projects/'+project.id+'/discovery/'+run.id+'/export';download.download='';actions.append(download);body.append(actions);

 if(run.error||run.message)body.append(node('p',run.error||run.message,'small'));if(run.summary)body.append(node('p',`${run.summary.candidates??0} candidates recorded · ${run.summary.reviewed??0} independently reviewed · ${run.summary.eligible??0} eligible before shortlist selection`,'small'));if(run.failures?.length)body.append(reportDetails('Run history · '+run.failures.length+' interruptions',run.failures.map(f=>`${f.phase||'Request'} · ${f.message||'Interrupted before completion'}`)));

 const epTitle=id=>{const ep=project.episodes.find(e=>e.id===id);return ep?'Episode '+ep.number:id||'Source window';};

 const coverageRows=coverage.map(c=>`${epTitle(c.episode_id)} · ${c.window_id||c.id||'Window'} · ${String(c.status||'pending').replaceAll('_',' ')}${c.reason?' · '+c.reason:''}${c.candidate_count!==undefined?' · '+c.candidate_count+' candidates':''}`);

 if(coverageRows.length)body.append(reportDetails(`Source coverage · ${coverageRows.length} windows`,coverageRows));else body.append(node('p','Coverage pending. No source windows have been recorded as scanned yet.','small'));

 const unscanned=(plan.windows||[]).filter(w=>!coverage.some(c=>(c.window_id||c.id)===w.id));if(unscanned.length)body.append(node('p',`${unscanned.length} planned windows have no completed coverage record yet.`,'small'));

 const shortlist=node('div',null,'discovery-shortlist');shortlist.append(node('h3','Ranked shortlist'));if(!run.shortlist?.length)shortlist.append(node('p',['complete','completed','done','no_qualifying'].includes(run.status)?'No cuts met the required source and editorial checks. The requested maximum was not filled.':'No qualifying cuts saved yet. Completed work stays available if this run is stopped.','small'));

 (run.shortlist||[]).forEach((g,index)=>{const row=node('article',null,'discovery-shortlist-row'),heading=node('div');heading.append(node('span',String(g.posting_priority??index+1),'discovery-rank'),node('h3',g.title||g.variations?.[0]?.angle||'Source cut'));row.append(heading,node('p',g.posting_reason||g.opportunity||'Review source and editorial checks.'));const cut=g.variations?.[0];row.append(node('p',`${cut?.validation?.duration??'?'}s · ${(g.source_ids||[]).map(epTitle).join(', ')}`,'small'));const open=node('button','Review this cut','quiet');open.disabled=busy;open.onclick=()=>attempt(()=>openDiscoveryRun(run.id,index));row.append(open);shortlist.append(row);});body.append(shortlist);

 const rejections=run.rejections||[];body.append(reportDetails(`Rejected candidates · ${rejections.length}`,rejections.length?rejections.map(r=>typeof r==='string'?r:`${r.title||r.candidate_id||r.promo_id||r.id||'Candidate'} · ${r.reason||r.reasons?.join('; ')||r.detail||r.stage||'Did not pass required checks'}`):['No rejection records saved. This does not imply pending windows were accepted.']));

}

$('discovery-mode').onclick=()=>go(4);$('manual-mode').onclick=()=>attempt(async()=>{await restoreManualProject();go(project?.groups?.length?3:project?1:0);});$('discovery-back').onclick=()=>go(project?.groups?.length?3:1);

$('discovery-form').addEventListener('input',invalidateDiscoveryPreview);$('discovery-form').addEventListener('change',invalidateDiscoveryPreview);

$('discovery-form').onsubmit=e=>{e.preventDefault();attempt(async()=>{text('discovery-error','');let body;try{body=discoveryBody();}catch(e){text('discovery-error',e.message);return;}setBusy(true,'Planning discovery locally…');try{const preview=await api('/projects/'+project.id+'/discovery/preview',body);discoveryPreview=preview;discoveryPreviewBody=body;const plan=preview.plan||preview,details=$('discovery-preview-details');details.replaceChildren();const calls=plan.estimated_calls||preview.estimated_calls||{},batches=plan.batch_count??preview.batch_count??plan.batches?.length??0,windows=plan.window_count??preview.window_count??plan.total_windows??plan.windows?.length??0,upper=preview.cost_upper_usd??plan.cost_upper_usd,remaining=preview.budget_remaining_usd;details.append(node('p',`${body.selected_ids.length} episodes · ${windows} source windows · ${batches} planned scan batches`),node('p',`${calls.minimum??'?'}–${calls.maximum??'?'} model requests estimated`),node('p',`Conservative cost estimate: ${Number.isFinite(upper)?'$'+upper.toFixed(3):'unavailable'} · Session budget remaining: ${Number.isFinite(remaining)?'$'+remaining.toFixed(3):'connect to see budget'} · Spending limit: $${Number(connection.budget||2).toFixed(2)}`));text('discovery-budget-warning',preview.within_budget===false?'The conservative estimate exceeds your remaining budget. This run may stop before finishing. Reduce the scope or resume later; a 50- or 100-episode show is not guaranteed to fit $2.':!connection.configured?'Connect Gemini before starting. Preview and saved reports need no key.':'The estimate is within the remaining budget. Actual requests and cost can vary.');$('discovery-preview').hidden=false;}finally{setBusy(false);}});};

$('start-discovery').onclick=()=>attempt(async()=>{if(!discoveryPreview||!discoveryPreviewBody)throw Error('Preview the scan again before starting.');if(discoveryPreview.model&&discoveryPreview.model!==connection.model){invalidateDiscoveryPreview();throw Error('The connected model changed. Preview the scan again for its updated request and cost estimate.');}const body=discoveryBody();if(JSON.stringify(body)!==JSON.stringify(discoveryPreviewBody)){invalidateDiscoveryPreview();throw Error('The brief changed. Preview the scan again before starting.');}await job('/projects/'+project.id+'/discovery',body);discoveryPreview=null;discoveryPreviewBody=null;$('discovery-preview').hidden=true;});

