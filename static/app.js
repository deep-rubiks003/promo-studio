'use strict';

const $=id=>document.getElementById(id),text=(id,value)=>$(id).textContent=value??'',clear=id=>$(id).replaceChildren();

const node=(tag,value,cls)=>{const n=document.createElement(tag);n.textContent=value??'';if(cls)n.className=cls;return n;};

let connection={},project=null,selected=new Set(),step=0,groupIndex=0,variationIndex=0,pending=[],busy=false,lastAction=null,assignments=new Map();

let activeJobId=null,watchedJob=null,recoveryJob=null,dismissedRecovery=new Set(),initialized=false;

let discoveryPreview=null,discoveryPreviewBody=null,selectedDiscoveryRun=null,activeDiscoveryRun=null;

function needsRepair(){return project?.pending_generation?.groups?.some(g=>g.variations?.some(v=>['reviewed','needs_repair'].includes(v.review_status)&&!v.validation?.eligible));}

const resumeLabel=()=>needsRepair()?'Repair rejected drafts':'Resume saved draft';

const stamp=s=>Number.isFinite(s)?`${Math.floor(s/60).toString().padStart(2,'0')}:${(s%60).toFixed(2).padStart(5,'0')}`:'Unavailable';

async function api(path,body){const response=await fetch('/api'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Studio-Token':connection.token},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'The request could not be completed.');return data;}

function error(e,action){if(['discovery','resume_discovery'].includes(e.operation)&&e.discoveryRunId&&project){action=()=>job('/projects/'+project.id+'/discovery/'+e.discoveryRunId+'/resume',{});}else if(e.operation==='discovery_spoilers'&&e.discoverySpoilerRunId&&project){action=()=>job('/projects/'+project.id+'/discovery/'+e.discoverySpoilerRunId+'/spoiler-check',{});}text('global-error-message',e.message||e);$('global-error').hidden=false;$('pending-work').hidden=!project?.pending_generation;savedOutputControls();const resumable=e.resumeGeneration&&project?.pending_generation;text('retry',resumable?resumeLabel():'Retry');lastAction=resumable?async()=>{await job('/projects/'+project.id+'/resume',{});go(3);}:action;}

async function attempt(fn){try{$('global-error').hidden=true;await fn();}catch(e){error(e,fn);}}

function setBusy(value,message=''){busy=value;$('activity').hidden=!value;text('activity-message',message);document.querySelectorAll('button,input,select').forEach(n=>{if(n.id==='stop-job')return;if(value){if(!('wasDisabled' in n.dataset))n.dataset.wasDisabled=String(n.disabled);n.disabled=true;}else if('wasDisabled' in n.dataset){n.disabled=n.dataset.wasDisabled==='true';delete n.dataset.wasDisabled;}});$('stop-job').hidden=!value||!activeJobId;if(value)$('stop-job').disabled=false;if(!value){renderNav();if(project)savedOutputControls();}}

async function refreshStatus(){connection=await api('/status');text('connection',connection.configured?'Gemini key connected':'Connect Gemini');text('connection-status',connection.configured?connection.model:'Local studio · No key needed to import');text('budget-status',connection.configured?`Budget used/held $${connection.spent.toFixed(3)} / $${connection.budget.toFixed(2)} · Includes failure reservations`:'Local studio · Import and episode selection use no AI');const picker=$('project-picker');picker.replaceChildren(new Option('Saved projects',''));connection.projects.forEach(p=>picker.add(new Option(p.name,p.id)));if(project){picker.value=project.id;renderDiscovery();}if(initialized)recoverStatus();}

function renderNav(){document.querySelectorAll('[data-step]').forEach(b=>{const n=Number(b.dataset.step);b.disabled=busy||(n>0&&!project)||(n===2&&!selected.size)||(n===3&&!project?.groups?.length);if(n===step)b.setAttribute('aria-current','step');else b.removeAttribute('aria-current');});$('choose-promos').disabled=busy||!selected.size;$('discovery-mode').disabled=busy||!project;$('manual-mode').disabled=busy;$('discovery-mode').setAttribute('aria-pressed',String(step===4||!!project?.discovery_view));$('manual-mode').setAttribute('aria-pressed',String(step!==4&&!project?.discovery_view));}

function go(n){if(busy||(n===2&&!selected.size))return;if(project?.discovery_view&&n!==3){attempt(async()=>{await restoreManualProject();go(n);});return;}step=n;syncModePresentation();if(n===1&&project)renderEpisodes();if(n===2&&project)renderBrief();if(n===3&&project)renderStudio();if(n===4&&project)renderDiscovery();document.querySelectorAll('.screen').forEach(s=>s.hidden=s.id!=='step-'+n);renderNav();}

async function watchJob(id){

 if(watchedJob===id)return;watchedJob=id;activeJobId=id;setBusy(true,'Recovering job progress…');

 const started=Date.now();let progressTicks=0;

 try{

  let result;

  do{result=await api('/jobs/'+id);if(['discovery','resume_discovery','discovery_episode_repair','discovery_episode_repairs','discovery_episode_review'].includes(result.operation)){if(step!==3){step=3;syncModePresentation();document.querySelectorAll('.screen').forEach(s=>s.hidden=s.id!=='step-3');}if(progressTicks++%5===0&&project?.id===result.project_id){const rid=result.run_id||result.discovery_run_id||activeDiscoveryRun||selectedDiscoveryRun;selectedDiscoveryRun=rid;if(rid){project=await api('/projects/'+project.id+'/discovery/'+rid+'/view');groupIndex=Math.min(groupIndex,Math.max(0,(project.groups?.length||0)-1));render();}renderStudio();}}text('activity-message',result.message||'Working…');text('activity-detail',`Completed work stays saved. ${Math.floor((Date.now()-started)/1000)}s in this view.`);$('stop-job').hidden=false;$('stop-job').disabled=result.status==='cancelling';$('stop-job').textContent=result.status==='cancelling'?'Stopping…':'Stop generation';if(['queued','running','cancelling'].includes(result.status))await new Promise(r=>setTimeout(r,1200));}while(['queued','running','cancelling'].includes(result.status));

  if(project?.id===result.project_id){const rid=result.run_id||result.discovery_run_id||activeDiscoveryRun||(String(result.operation).includes('discovery')?project.discovery_view:null);project=await api('/projects/'+project.id);if(rid){selectedDiscoveryRun=rid;project=await api('/projects/'+project.id+'/discovery/'+rid+'/view');groupIndex=Math.max(0,(project.groups||[]).findIndex(g=>g.episode_id===result.body?.episode_id));variationIndex=0;}render();if(rid){step=3;syncModePresentation();document.querySelectorAll('.screen').forEach(s=>s.hidden=s.id!=='step-3');renderStudio();renderNav();}}

  if(result.status==='error'){const failure=Error(result.error||result.message);failure.operation=result.operation;failure.resumeGeneration=['generate','resume_generation'].includes(result.operation);if(['discovery','resume_discovery','discovery_episode_repair','discovery_episode_repairs','discovery_episode_review'].includes(result.operation))failure.discoveryRunId=result.run_id||result.discovery_run_id||activeDiscoveryRun;if(result.operation==='discovery_spoilers')failure.discoverySpoilerRunId=result.run_id||result.discovery_run_id||activeDiscoveryRun;throw failure;}

  if(['cancelled','interrupted'].includes(result.status))showRecovery(result);
  return result;

 }finally{activeJobId=null;watchedJob=null;setBusy(false);await refreshStatus();if(step===4)renderDiscovery();}

}

async function job(path,body){

 if(busy)return;

 if(!connection.configured){$('connect-dialog').showModal();throw Error('Connect Gemini to create drafts or run AI checks.');}

 $('recovery-notice').hidden=true;setBusy(true,'Starting…');

 try{const {job_id,run_id}=await api(path,body);activeDiscoveryRun=run_id||(path.includes('/discovery/')?project.discovery_view:null);if(run_id){selectedDiscoveryRun=run_id;project=await api('/projects/'+project.id+'/discovery/'+run_id+'/view');groupIndex=variationIndex=0;step=3;syncModePresentation();render();document.querySelectorAll('.screen').forEach(s=>s.hidden=s.id!=='step-3');renderStudio();$('discovery-preview').close();spotlightEpisodeProgress();}const completed=await watchJob(job_id);activeDiscoveryRun=null;return completed;}

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



function renderStudio(){const groups=project.groups||[];const picker=$('group-select');picker.replaceChildren();groups.forEach((g,i)=>{const v=g.variations?.[0],ep=project.episodes.find(ep=>ep.id===(g.episode_id||g.source_ids?.[0]));const score=discoveryRating(v);const label=project.discovery_view?`#${g.posting_priority??i+1} · Ep ${ep?.number??'?'} · ${g.title||v?.angle||'Episode draft'} · ${score===null?'Not rated':score+'/100'} · ${v?.validation?.eligible?'Passed':'Needs repair'}`:(g.title||`Story ${i+1}`)+(v?.validation?.eligible?' · Passed':' · Needs repair');picker.add(new Option(label,i));});;if(groupIndex>=groups.length)groupIndex=0;picker.value=groupIndex;const v=activeVariation();$('studio-empty').hidden=!!v;$('variation-panel').hidden=!v;clear('angle-tabs');$('angle-tabs').hidden=true;text('group-opportunity',groups[groupIndex]?.opportunity||'');const suffix=project.saved_output_view?'?saved=true':'';$('export').href='/api/projects/'+project.id+'/export.pdf'+suffix;$('json-export').href='/api/projects/'+project.id+'/export'+suffix;$('handoff-export').href='/api/projects/'+project.id+'/handoff'+suffix;if(!v){$('spoiler-result').replaceChildren();savedOutputControls();return;}groups[groupIndex].variations.slice(0,1).forEach((x,i)=>{const tab=node('button',x.angle||`Variation ${i+1}`);tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(i===variationIndex));tab.onclick=()=>{variationIndex=i;renderStudio();};$('angle-tabs').append(tab);});text('cut-title',v.angle);renderTimeline(v);text('studio-title',`The cut room. ${groupIndex+1} / ${groups.length}`);text('angle-purpose',v.distinction);text('studio-source',v.segments.map(s=>{const ep=project.episodes.find(e=>e.id===s.episode_id);return `${ep?.title||'Unknown episode'} · ${stamp(s.start)}–${stamp(s.end)}`;}).join(' → '));text('cut-duration',`${v.validation.duration}s`);text('cut-mode',project.brief.structure);text('eligibility',v.validation.eligible?'Eligible source cut':'Needs another cut · Failed checks');const weights={'Hook':25,'Standalone clarity':25,'Escalation':15,'Cliffhanger':25,'Cut quality':10};const scores=new Map((v.rubric||[]).map(r=>[r.criterion,r.score]));const rubricScore=v.validation.rubric_score??(v.review_status!=='pending'&&Object.keys(weights).every(k=>Number.isFinite(scores.get(k)))?Math.round(Object.keys(weights).reduce((n,k)=>n+scores.get(k)*weights[k]/5,0)):null);text('potential-score',rubricScore===null?'—':rubricScore+'/100');text('score-state',rubricScore===null?'Optional review not run. Run Virality Review to get a score; cut-check warnings remain visible.':(v.validation.eligible?'Review complete · Required checks passed.':'Review complete · Required checks still need repair.'));text('batch-summary',`${groups.length} promos · ${groups.filter(g=>g.variations[0]?.validation?.eligible).length} passed · ${groups.filter(g=>!g.variations[0]?.validation?.eligible).length} need repair`);$('repair-current').hidden=!!v.validation.eligible||!!project.saved_output_view;$('repair-current').disabled=busy; text('unanswered-question',v.question);showEvidence($('story-beats'),v.validation);clear('rubric');(v.rubric||[]).forEach(r=>{const row=node('div',null,'rubric-row');row.append(node('strong',`${r.criterion} · ${r.score}/5`),node('p',r.reason));$('rubric').append(row);});clear('checks');v.validation.checks.forEach(c=>{const row=node('p',`${c.passed?'Pass':'Fail'} · ${c.name}`);const detail=node('details',null,'check-detail');detail.append(node('summary',`${c.passed?'Pass':'Fail'} · ${c.name}`),node('p',c.detail));$('checks').append(detail);});text('checks-count',`${v.validation.checks.filter(c=>c.passed).length}/${v.validation.checks.length}`);text('editor-note',[v.weakness,v.review_notes].filter(Boolean).join(' '));clear('cut-warnings');v.validation.warnings.forEach(w=>$('cut-warnings').append(node('p',w)));if(v.review_status==='pending'&&!project.discovery_view){const resume=node('button','Resume unfinished reviews','button secondary');resume.onclick=()=>attempt(async()=>{await job('/projects/'+project.id+'/resume',{});renderStudio();});$('cut-warnings').append(resume);}renderSpoiler(v);$('run-virality-review').disabled=busy||!!project.saved_output_view;text('run-virality-review',v.review_status==='reviewed'?'Run Virality Review again':'Run Virality Review');$('repair-failed').hidden=!!project.saved_output_view||!failedItems(repairScope()).length;$('review-pending-repairs').hidden=!scopeItems(repairScope()).some(row=>row.proposal);const proposal=project.proposals[v.id];$('proposal').hidden=!proposal;if(proposal){const area=$('proposal');area.replaceChildren(node('h3','A proposed cut is ready'),node('p',proposal.change));const compare=node('button','Compare proposed cut','button secondary');compare.onclick=()=>openRepairComparison(v.id);area.append(compare);}savedOutputControls();}



function filesChosen(files){pending=Array.from(files);if(pending.some(f=>!f.name.toLowerCase().endsWith('.txt'))){text('upload-feedback','Choose TXT files only.');pending=[];}$('pending-files').replaceChildren(...pending.map(f=>node('li',f.name)));$('import-project').disabled=!pending.length;text('upload-feedback',pending.length?`${pending.length} files ready to import`:'Choose timestamped TXT files.');}

$('choose-files').onclick=()=>$('files').click();$('files').onchange=e=>filesChosen(e.target.files);$('dropzone').ondragover=e=>e.preventDefault();$('dropzone').ondrop=e=>{e.preventDefault();filesChosen(e.dataTransfer.files);};

$('upload-form').onsubmit=e=>{e.preventDefault();attempt(async()=>{setBusy(true,'Extracting episode numbers and loglines locally…');try{const files=await Promise.all(pending.map(async f=>({name:f.name,text:await f.text()})));project=await api('/projects',{name:$('upload-name').value,files});selected=new Set();hydrateBrief();render();await refreshStatus();}finally{setBusy(false);}go(1);});};

$('select-all').onchange=e=>{selected=new Set(e.target.checked?project.episodes.map(ep=>ep.id):[]);renderEpisodes();};$('apply-range').onclick=()=>{const a=project.episodes.findIndex(ep=>ep.id===$('range-from').value),b=project.episodes.findIndex(ep=>ep.id===$('range-to').value);selected=new Set(project.episodes.slice(Math.min(a,b),Math.max(a,b)+1).map(ep=>ep.id));renderEpisodes();};$('choose-promos').onclick=()=>go(2);$('change-episodes').onclick=()=>go(1);

$('promo-mode').onchange=renderBrief;$('grouping-mode').onchange=renderBrief;

async function generate(){const episode_groups=pools();await job('/projects/'+project.id+'/generate',{selected_ids:selectedEpisodes().map(ep=>ep.id),promo_mode:$('promo-mode').value,episode_groups,minimum:Number($('min-duration').value),maximum:Number($('max-duration').value),structure:$('structure').value,emphasis:$('emphasis').value});groupIndex=variationIndex=0;renderStudio();go(3);}

$('brief-form').onsubmit=e=>{e.preventDefault();if(!selected.size){text('brief-error','Select at least one episode.');return;}if(project.groups.length)$('replace-dialog').showModal();else attempt(generate);};$('confirm-replace').onclick=()=>{$('replace-dialog').close();attempt(generate);};$('cancel-replace').onclick=()=>$('replace-dialog').close();$('structure').onchange=()=>text('structure-note',$('structure').value==='continuous'?'One uninterrupted source sequence. A multi-episode pool chooses its strongest continuous moment.':'Ordered source segments can span the assigned episodes.');$('group-select').onchange=()=>{groupIndex=Number($('group-select').value);variationIndex=0;renderStudio();};

$('repair-current').onclick=()=>attempt(async()=>{if(project.discovery_view){const g=project.groups[groupIndex],eid=g.episode_id||g.source_ids?.[0];await job('/projects/'+project.id+'/discovery/'+project.discovery_view+'/episodes/'+encodeURIComponent(eid)+'/repair',{});return;}const pid=project.id,vid=activeVariation().id;text('repair-current','Repairing promo…');try{await job('/projects/'+pid+'/refine',{variation_id:vid,part:'Whole promo',prompt:'Repair the failed checks using the reviewer reasons. Follow one causal storyline with enough connecting context. Remove unrelated subplots. Keep the opening conflict and end before its resolution, with complete source dialogue and requested timing. Propose one corrected cut.'});if(project?.id===pid&&project.proposals?.[vid])openRepairComparison(vid);}finally{text('repair-current','Repair this promo');}});

$('check-spoilers').onclick=()=>attempt(()=>job('/projects/'+project.id+(project.discovery_view?'/discovery/'+project.discovery_view:'')+'/spoiler-check',{}));

$('refine-form').onsubmit=e=>{e.preventDefault();attempt(()=>job('/projects/'+project.id+'/refine',{variation_id:activeVariation().id,prompt:$('prompt').value,part:$('refine-target').value}));};document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{$('prompt').value=b.dataset.prompt;$('prompt').focus();});

$('score-info').onclick=()=>{$('score-explanation').hidden=!$('score-explanation').hidden;$('score-info').setAttribute('aria-expanded',String(!$('score-explanation').hidden));};

document.querySelectorAll('[data-step]').forEach(b=>b.onclick=()=>{const n=Number(b.dataset.step);if(project?.discovery_view)attempt(async()=>{await restoreManualProject();go(n);});else go(n);});$('adjust-brief').onclick=()=>go(project?.discovery_view?4:2);$('connection').onclick=()=>{$('model').value=connection.model||'gemini-3.5-flash-lite';$('budget').value=connection.budget??2;$('connect-dialog').showModal();};$('close-connect').onclick=()=>$('connect-dialog').close();$('connect-form').onsubmit=async e=>{e.preventDefault();$('save-connect').disabled=true;text('connect-error','Checking model access…');try{await api('/connect',{key:$('api-key').value,model:$('model').value,budget:Number($('budget').value)});$('api-key').value='';await refreshStatus();$('connect-dialog').close();text('connect-error','');}catch(e){text('connect-error',e.message);}finally{$('save-connect').disabled=false;}};

$('project-picker').onchange=()=>{if($('project-picker').value)attempt(()=>openProject($('project-picker').value));};$('new-project').onclick=()=>{discoveryPreview=null;discoveryPreviewBody=null;selectedDiscoveryRun=null;activeDiscoveryRun=null;project=null;selected.clear();assignments.clear();go(0);text('project-name','New project');};$('home').onclick=e=>{e.preventDefault();go(0);};$('dismiss-error').onclick=()=>$('global-error').hidden=true;$('retry').onclick=()=>{if(lastAction)attempt(lastAction);};

$('view-saved-output').onclick=()=>attempt(viewSavedOutput);

$('resume-pending').onclick=()=>attempt(async()=>{await job('/projects/'+project.id+'/resume',{});go(3);});

$('new-project').addEventListener('click',()=>{$('pending-work').hidden=true;});

attempt(async()=>{await refreshStatus();const target=new URLSearchParams(location.search).get('project');if(target&&connection.projects.some(p=>p.id===target))await openProject(target);initialized=true;recoverStatus();});

/* Show-wide discovery is an independent source-backed run, never a manual draft replacement. */

function discoveryRun(){const id=selectedDiscoveryRun||project?.discovery_view;return project?.discovery_runs?.find(r=>r.id===id)||(project?.discovery_report?.id===id?project.discovery_report:null)||null;}

function runDate(run){const raw=run.created_at||run.created;if(!raw)return 'Saved run';const date=new Date(typeof raw==='number'?raw*1000:raw);return Number.isNaN(date.getTime())?'Saved run':date.toLocaleString();}

function runStatus(run){const job=[...(connection.active_jobs||[]),...(connection.latest_jobs||[])].find(j=>j.run_id===run.id||j.discovery_run_id===run.id);const status=run.status==='done'?'done':job&&['error','interrupted','cancelled'].includes(job.status)?job.status:run.status;const labels={queued:'Queued',running:'Scanning',completed:'Complete',complete:'Complete',done:'Complete',cancelled:'Stopped',interrupted:'Interrupted',error:'Needs attention',pending:'Pending',no_qualifying:'No qualifying cuts'};if(status==='done'&&run.summary?.failed_windows)return 'Complete · source coverage gaps';if(status==='done'&&run.summary?.failed_reviews)return 'Complete · review gaps';return labels[status]||String(status||'Saved').replaceAll('_',' ');}

function discoveryCounts(run){const coverage=Array.isArray(run.coverage)?run.coverage:[],scanned=coverage.filter(c=>['scanned','complete','completed','no_candidate','no_candidates','reviewed'].includes(c.status)||c.scanned===true).length;return {total:run.summary?.total_windows??run.plan?.windows?.length??coverage.length,scanned:run.summary?.scanned_windows??scanned,shortlist:run.shortlist?.length||0,rejected:run.rejections?.length||0};}

function runCoverageSummary(run){const c=discoveryCounts(run);return `${c.scanned} / ${c.total} source windows scanned · ${Math.max(0,c.total-c.scanned)} pending · ${c.shortlist} ranked · ${c.rejected} cut-check history records`;}

async function restoreManualProject(){if(!project?.discovery_view)return;const ids=new Set(selected);project=await api('/projects/'+project.id);selected=ids;groupIndex=variationIndex=0;hydrateBrief();render();}

async function openDiscoveryRun(rid,index=0){if(busy||!project)return;selectedDiscoveryRun=rid;project=await api('/projects/'+project.id+'/discovery/'+encodeURIComponent(rid)+'/view');groupIndex=index;variationIndex=0;render();go(3);}

function savedOutputControls(){manualSavedOutputControls();renderLiveEpisodeProgress();$('review-many').hidden=!!project?.saved_output_view||!project?.groups?.length;$('review-many').disabled=busy||reviewBatchRunning;const discovery=!!project?.discovery_view;for(const id of ['export','handoff-export'])$(id).hidden=discovery;$('export').hidden=false;if(!discovery){text('repair-current','Repair this promo');document.querySelector('.group-picker>span').textContent='Promo';text('export','Download all cuts · PDF');text('adjust-brief','Adjust the brief');text('json-export','Source data');const empty=$('studio-empty');empty.querySelector('h2').textContent='No eligible cuts yet.';empty.querySelector('p').textContent='Choose your episodes and brief to create one draft per promo.';return;}

 const run=discoveryRun()||{},notice=$('saved-output-notice');notice.hidden=false;notice.replaceChildren(node('strong',`${runStatus(run)} · ${runCoverageSummary(run)}`),node('p','Discovery output · Your manual drafts are preserved. Saved cuts can be opened without a key. Refinement is not available for discovery cuts.'));

 const back=node('button','Coverage & run report','quiet');back.onclick=()=>go(4);notice.append(back);const manual=node('button','Return to manual drafts','quiet');manual.onclick=()=>attempt(async()=>{await restoreManualProject();go(project.groups?.length?3:1);});notice.append(manual);

 $('run-virality-review').disabled=busy||!activeVariation();text('studio-title','Ranked episode promos');document.querySelector('.group-picker>span').textContent='Posting order';const group=project.groups?.[groupIndex];if(group){text('group-opportunity',`Posting priority ${group.posting_priority??groupIndex+1} · ${group.posting_reason||group.opportunity||'Review the source and editorial checks before posting.'}`);}

 $('pending-work').hidden=true;document.querySelector('.refine').hidden=true;$('repair-current').hidden=!run.plan?.per_episode||!!activeVariation()?.validation?.eligible;text('repair-current','Repair this episode promo');$('repair-current').disabled=busy;$('proposal').hidden=true;$('export').hidden=false;$('export').href='/api/projects/'+project.id+'/discovery/'+project.discovery_view+'/export.pdf';text('export','Download ranked promos · PDF');$('handoff-export').hidden=true;$('json-export').href='/api/projects/'+project.id+'/discovery/'+project.discovery_view+'/export';text('json-export','Discovery report · JSON');text('adjust-brief','Review discovery report');

 const empty=$('studio-empty');empty.querySelector('h2').textContent=busy?'Finding your first episode promo…':'No source cuts in this run yet.';empty.querySelector('p').textContent=busy?'Ranked cuts appear here as episodes finish. You can stop generation without losing completed work.':'Review coverage and cut-check reasons. A stopped or incomplete run can be resumed from its report.';

 $('check-spoilers').hidden=false;$('check-spoilers').disabled=busy||!run.shortlist?.length||run.status!=='done';text('spoiler-help','Optional: one batch request checks every ranked promo. Saved findings follow the selected promo.');

}

function discoveryBody(){const ids=project.episodes.map(e=>e.id);if(!ids.length)throw Error('Upload timestamped episode scripts first.');const minimum=Number($('discovery-min').value),maximum=Number($('discovery-max').value);if(minimum<20||maximum>60||minimum>maximum)throw Error('Choose a duration between 20 and 60 seconds.');return {selected_ids:ids,structure:$('discovery-structure').value,quantity:'all',per_episode:true,minimum,maximum,emphasis:$('discovery-emphasis').value};}

function invalidateDiscoveryPreview(){discoveryPreview=null;discoveryPreviewBody=null;$('discovery-preview').close();text('discovery-error','');renderDiscoveryScope();}

function syncModePresentation(){const full=step===4||!!project?.discovery_view;document.body.classList.toggle('show-discovery',full);document.querySelector('.flow').hidden=full;document.querySelector('.studio-modes').hidden=full||step===0||!project;}
function paintEpisodeMap(map,run){
 const prior=new Map([...map.children].map(c=>[c.dataset.episodeId,{state:c.dataset.state,at:Number(c.dataset.completedAt||0)}])),coverage=run?.coverage||[];
 map.replaceChildren();let completed=0;
 const allDone=run?.status==='done';
 const statuses=['scanned','no_candidate','complete','completed','no_candidates','reviewed'];
 const pending=project.episodes.find(ep=>{const rows=coverage.filter(c=>c.episode_id===ep.id);return rows.some(c=>!statuses.includes(c.status)&&c.status!=='failed');});
 project.episodes.forEach(ep=>{const rows=coverage.filter(c=>c.episode_id===ep.id),done=rows.length>0&&rows.every(c=>statuses.includes(c.status)),failed=rows.some(c=>c.status==='failed'),working=!done&&!failed&&!allDone&&busy&&pending?.id===ep.id;
 const state=done?'scanned':failed?'failed':working?'processing':'pending';if(done)completed++;
 const cell=node('span',String(ep.number),'episode-tile '+state);cell.dataset.episodeId=ep.id;cell.dataset.state=state;
 cell.title=`Episode ${ep.number} · ${done?'Source scan complete':failed?'Source window failed':working?'Processing':'Pending scan'}`;cell.setAttribute('aria-label',cell.title);
 const old=prior.get(ep.id),at=done&&old&&old.state!=='scanned'?Date.now():old?.at||0;cell.dataset.completedAt=String(at);if(done&&at&&Date.now()-at<1200)cell.classList.add('just-completed');map.append(cell);
 });return completed;
}
function renderLiveEpisodeProgress(){
 const panel=$('live-episode-progress');panel.hidden=!project?.discovery_view;if(panel.hidden)return;
 const run=discoveryRun(),count=paintEpisodeMap($('live-episode-map'),run),total=project.episodes.length;
 text('live-progress-count',`${count} / ${total} scanned`);$('live-progress-fill').style.width=(total?count/total*100:0)+'%';
 panel.classList.toggle('is-scanning',busy&&run?.status!=='done');
}
function spotlightEpisodeProgress(){requestAnimationFrame(()=>{const panel=$('live-episode-progress');if(panel.hidden)return;panel.classList.remove('scan-started');void panel.offsetWidth;panel.classList.add('scan-started');panel.focus({preventScroll:true});panel.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});});}
function renderDiscoveryScope(){if(!project)return;text('discovery-show-name',project.name);text('discovery-episode-count',project.episodes.length);text('discovery-scope-summary',`All ${project.episodes.length} uploaded episodes · One promo per episode · Original timecodes`);const ledger=$('discovery-source-ledger');if(!ledger.dataset.userOpened)ledger.open=innerWidth>700;ledger.querySelector('summary').onclick=()=>ledger.dataset.userOpened='true';paintEpisodeMap($('discovery-episode-map'),discoveryRun());}


function renderDiscovery(){if(!project)return;renderDiscoveryScope();const area=$('discovery-history');area.replaceChildren();const runs=[...(project.discovery_runs||[])].reverse();if(!runs.length){area.append(node('p','No discovery runs yet. Preview a scan to see the source coverage and estimated cost before starting.','discovery-empty'));$('discovery-run-report').hidden=true;return;}

 if(!selectedDiscoveryRun||!runs.some(r=>r.id===selectedDiscoveryRun))selectedDiscoveryRun=runs[0].id;

 runs.forEach((run,i)=>{const row=node('div',null,'discovery-history-row'),copy=node('div');copy.append(node('strong',`${runStatus(run)} · ${run.shortlist?.length||0} ranked`),node('p',`${run.plan?.selected_ids?.length||0} episodes · ${run.plan?.structure||'either'} · ${runDate(run)}`,'small'));const button=node('button',selectedDiscoveryRun===run.id?'Viewing report':'Review report','quiet');button.setAttribute('aria-pressed',String(selectedDiscoveryRun===run.id));button.onclick=()=>{selectedDiscoveryRun=run.id;renderDiscovery();};row.append(copy,button);area.append(row);});

 const run=runs.find(r=>r.id===selectedDiscoveryRun);$('discovery-run-report').hidden=!run;if(run)renderDiscoveryReport(run);renderDiscoveryScope();

}

function reportDetails(title,rows){const details=node('details',null,'discovery-details');details.append(node('summary',title));const list=node('div');rows.forEach(row=>list.append(node('p',row)));details.append(list);return details;}

function discoveryRating(v){if(!v)return null;const scores=new Map((Array.isArray(v.rubric)?v.rubric:[]).map(r=>[r.criterion,r.score]));const weights={'Hook':25,'Standalone clarity':25,'Escalation':15,'Cliffhanger':25,'Cut quality':10};return Object.keys(weights).every(k=>Number.isFinite(scores.get(k)))?Math.round(Object.entries(weights).reduce((n,[k,w])=>n+scores.get(k)*w/5,0)):null;}
function discoveryCheckSummary(v){const box=node('div',null,'discovery-check-summary');const rating=discoveryRating(v);box.append(node('strong',rating===null?'Not rated · No completed editorial review':`Editorial rating ${rating}/100`));const checks=v?.validation?.checks||[];const failed=checks.filter(c=>!c.passed);if(!v?.validation?.eligible){box.append(node('p','Needs repair · Review before posting','error'));failed.forEach(c=>box.append(node('p',c.name+': '+c.detail)));if(v?.review_notes)box.append(node('p',v.review_notes));}else box.append(node('p','Required cut checks passed.'));return box;}
function episodeRepairButton(run,eid){const b=node('button','Repair this episode promo','button secondary'),row=run.episode_results?.find(row=>row.episode_id===eid);b.hidden=row?.draft?!!row.draft.validation?.eligible:row?.status!=='needs_repair';b.disabled=busy;b.onclick=()=>openRepairPicker(run.id,eid);return b;}

function renderDiscoveryReport(run){const body=$('discovery-report-body');body.replaceChildren();const plan=run.plan||{},coverage=Array.isArray(run.coverage)?run.coverage:[];body.append(node('h3',runStatus(run)),node('p',runCoverageSummary(run),'discovery-coverage-summary'),node('p',`${plan.selected_ids?.length||0} episodes · ${plan.minimum??20}–${plan.maximum??60}s · ${plan.structure||'either'} · ${plan.per_episode?'One promo per episode':plan.quantity==='all'?'Legacy run · All qualifying distinct promos':plan.quantity==='cap'?'Legacy run · Up to '+plan.limit:'Legacy run'}`,'small'));if(run.summary?.failed_windows)body.append(node('p',`${run.summary.failed_windows} source windows need attention. This result has coverage gaps; inspect the run history before treating the show as fully scanned.`,'discovery-gap'));if(run.episode_results?.length){const outcomes=run.episode_results;body.append(node('p',`${outcomes.length} episodes accounted for · ${outcomes.filter(r=>r.draft).length} available drafts · ${outcomes.filter(r=>r.status==='ready').length} ready · ${outcomes.filter(r=>r.status==='needs_repair').length} need repair · ${outcomes.filter(r=>r.status==='pending').length} pending`,'small'));}if(plan.emphasis)body.append(node('p','Emphasis: '+plan.emphasis,'small'));

 body.append(node('p','Coverage means these source windows were processed. It does not guarantee every possible promo was found. Unscanned or pending work is not a rejection.','small'));

 const actions=node('div',null,'discovery-run-actions');const batchReview=node('button','Review multiple promos','button secondary');batchReview.disabled=busy||reviewBatchRunning||!(run.shortlist?.length);batchReview.onclick=()=>openReviewPicker(run.id);actions.append(batchReview);const view=node('button','Open ranked cuts','button secondary');view.disabled=busy||!(run.shortlist?.length);view.onclick=()=>attempt(()=>openDiscoveryRun(run.id));actions.append(view);

 if(!['complete','completed','done','no_qualifying'].includes(run.status)){const resume=node('button','Resume this run','button primary');resume.disabled=busy;resume.onclick=()=>attempt(async()=>{await job('/projects/'+project.id+'/discovery/'+run.id+'/resume',{});});actions.append(resume);}

 const pdf=node('a','Download ranked promos · PDF','button secondary');pdf.href='/api/projects/'+project.id+'/discovery/'+run.id+'/export.pdf';pdf.download='';actions.append(pdf);const failed=failedItems({pid:project.id,rid:run.id});if(failed.length){const repair=node('button',`Repair failed promos (${failed.length})`,'button secondary');repair.disabled=busy;repair.onclick=()=>openRepairPicker(run.id);actions.append(repair);}if(Object.keys(run.repair_proposals||{}).length){const review=node('button','Review saved repairs','button secondary');review.disabled=busy;review.onclick=()=>reviewPendingRepairs({pid:project.id,rid:run.id});actions.append(review);}const download=node('a','Download run report · JSON','quiet');download.href='/api/projects/'+project.id+'/discovery/'+run.id+'/export';download.download='';actions.append(download);body.append(actions);

 if(run.error||run.message)body.append(node('p',run.error||run.message,'small'));if(run.summary)body.append(node('p',`${run.summary.batches_completed??run.completed_batches?.length??0} / ${run.summary.batches_total??run.plan?.batches?.length??0} scan batches saved · ${run.summary.scanned_events??0} / ${run.summary.total_events??0} source events scanned · Phase: ${run.phase||'queued'}`,'small'));if(run.summary)body.append(node('p',`${run.summary.candidates??0} candidates recorded · ${run.summary.reviewed??0} independently reviewed · ${run.summary.eligible??0} eligible before final ranking`,'small'));if(run.failures?.length)body.append(reportDetails('Run history · '+run.failures.length+' interruptions',run.failures.map(f=>`${f.phase||'Request'} · ${f.message||'Interrupted before completion'}`)));

 const epTitle=id=>{const ep=project.episodes.find(e=>e.id===id);return ep?'Episode '+ep.number:id||'Source window';};

 const coverageRows=coverage.map(c=>`${epTitle(c.episode_id)} · ${c.window_id||c.id||'Window'} · ${String(c.status||'pending').replaceAll('_',' ')}${c.reason?' · '+c.reason:''}${c.candidate_count!==undefined?' · '+c.candidate_count+' candidates':''}`);

 if(coverageRows.length)body.append(reportDetails(`Source coverage · ${coverageRows.length} windows`,coverageRows));else body.append(node('p','Coverage pending. No source windows have been recorded as scanned yet.','small'));

 const unscanned=(plan.windows||[]).filter(w=>!coverage.some(c=>(c.window_id||c.id)===w.id));if(unscanned.length)body.append(node('p',`${unscanned.length} planned windows have no completed coverage record yet.`,'small'));

 const shortlist=node('div',null,'discovery-shortlist');shortlist.append(node('h3','Ranked promos'));if(!run.shortlist?.length)shortlist.append(node('p',['complete','completed','done','no_qualifying'].includes(run.status)?'No usable source drafts are saved yet. Inspect each episode outcome below and try repair.':'No qualifying cuts saved yet. Completed work stays available if this run is stopped.','small'));

 (run.shortlist||[]).forEach((g,index)=>{const row=node('article',null,'discovery-shortlist-row'),heading=node('div');heading.append(node('span',String(g.posting_priority??index+1),'discovery-rank'),node('h3',g.title||g.variations?.[0]?.angle||'Source cut'));row.append(heading,node('p',g.posting_reason||g.opportunity||'Review source and editorial checks.'));const cut=g.variations?.[0];row.append(discoveryCheckSummary(cut));if(plan.per_episode&&g.source_ids?.length===1)row.append(episodeRepairButton(run,g.source_ids[0]));row.append(node('p',`${cut?.validation?.duration??'?'}s · ${(g.source_ids||[]).map(epTitle).join(', ')}`,'small'));const sequence=node('ol',null,'ranked-source-cuts');(Array.isArray(cut?.segments)?cut.segments:[]).filter(s=>s&&typeof s==='object').forEach(segment=>sequence.append(node('li',`${epTitle(segment.episode_id)} · ${stamp(segment.start)} — ${stamp(segment.end)}`)));row.append(sequence);const beats=cut?.validation?.evidence||[];for(const role of ['Hook','Cliffhanger']){const beat=beats.find(b=>b.role===role);const part=node('div',null,'ranked-story-part');part.append(node('strong',role),node('p',beat?.reason||'Inspect source evidence in the cut.'));const events=beat?.events||[],event=role==='Hook'?(events.find(e=>e.kind==='dialogue')||events[0]):(events.filter(e=>e.kind==='dialogue').at(-1)||events.at(-1));if(event){const quote=node('details',null,'ranked-quote');quote.append(node('summary','Source line · '+stamp(event.start)),node('p',event.text));part.append(quote);}row.append(part);}if(cut?.question)row.append(node('p','Left unanswered: '+cut.question,'ranked-question'));const open=node('button','Review this cut','quiet');open.disabled=busy||!cut?.validation;open.onclick=()=>attempt(()=>openDiscoveryRun(run.id,index));row.append(open);shortlist.append(row);});body.append(shortlist);

 if(run.episode_results?.length){const episodes=node('section',null,'discovery-episode-results');episodes.append(node('h3','Every episode accounted for'));run.episode_results.forEach(result=>{const item=node('details',null,'discovery-details'),ep=project.episodes.find(e=>e.id===result.episode_id);const label={ready:'Ready · Included in ranking',pending:'Pending',needs_repair:'Needs repair'};item.append(node('summary',`Episode ${ep?.number??result.episode_number??'?'} · ${label[result.status]||result.status}`));const info=node('div');const draft=result.draft;info.append(discoveryCheckSummary(draft));if(plan.per_episode)info.append(episodeRepairButton(run,result.episode_id));if(result.last_repair?.message)info.append(node('p','Last repair · '+result.last_repair.message));if(draft){info.append(node('strong',draft.angle||'Saved episode draft'));(Array.isArray(draft.segments)?draft.segments:[]).filter(s=>s&&typeof s==='object').forEach(segment=>info.append(node('p',`${epTitle(segment.episode_id)} · ${stamp(segment.start)} — ${stamp(segment.end)}`)));if(draft.question)info.append(node('p','Left unanswered: '+draft.question));}const reasons=Array.isArray(result.reasons)?result.reasons:[];reasons.forEach(reason=>info.append(node('p',typeof reason==='string'?reason:JSON.stringify(reason))));if(!draft&&result.status==='pending')info.append(node('p','This episode has unfinished source or review work.'));if(!draft&&result.status==='needs_repair')info.append(node('p','No usable source cut was returned. Try repair; no dialogue or timestamps have been invented.'));item.append(info);episodes.append(item);});body.append(episodes);}const rejections=run.rejections||[];body.append(node('p','This history counts proposed cut attempts, not episodes removed. Each episode’s available draft stays in the results above.','small'));body.append(reportDetails(`Other cut attempts & check history · ${rejections.length}`,rejections.length?rejections.map(r=>typeof r==='string'?r:`${r.title||r.candidate_id||r.promo_id||r.id||'Candidate'} · ${r.reason||r.reasons?.join('; ')||r.detail||r.stage||'Did not pass required checks'}`):['No failed cut attempts recorded.']));

}

$('discovery-mode').onclick=()=>go(4);$('manual-mode').onclick=()=>attempt(async()=>{await restoreManualProject();go(project?.groups?.length?3:project?1:0);});$('discovery-back').onclick=()=>attempt(async()=>{await restoreManualProject();go(project?.groups?.length?3:1);});

$('discovery-form').addEventListener('input',invalidateDiscoveryPreview);$('discovery-form').addEventListener('change',invalidateDiscoveryPreview);

$('discovery-form').onsubmit=e=>{e.preventDefault();attempt(async()=>{text('discovery-error','');let body;try{body=discoveryBody();}catch(e){text('discovery-error',e.message);return;}setBusy(true,'Planning discovery locally…');try{const preview=await api('/projects/'+project.id+'/discovery/preview',body);discoveryPreview=preview;discoveryPreviewBody=body;const plan=preview.plan||preview,details=$('discovery-preview-details');details.replaceChildren();const calls=plan.estimated_calls||preview.estimated_calls||{},batches=plan.batch_count??preview.batch_count??plan.batches?.length??0,windows=plan.window_count??preview.window_count??plan.total_windows??plan.windows?.length??0,upper=preview.cost_upper_usd??plan.cost_upper_usd,remaining=preview.budget_remaining_usd;details.append(node('p',`${body.selected_ids.length} episodes · ${windows} source windows · ${batches} planned scan batches`),node('p',`${calls.minimum??'?'}–${calls.maximum??'?'} model requests · upper bound includes all fallback splits`),node('p',`Worst-case failure/retry cost ceiling: ${Number.isFinite(upper)?'$'+upper.toFixed(3):'unavailable'} · Session budget remaining: ${Number.isFinite(remaining)?'$'+remaining.toFixed(3):'connect to see budget'} · Spending limit: $${Number(connection.budget||2).toFixed(2)}`));text('discovery-budget-warning',preview.within_budget===false?'The worst-case retry ceiling exceeds your remaining budget. This run may stop before finishing. Completed batches stay saved; resume when quota or budget permits; completion depends on your chosen spending limit and provider quota.':!connection.configured?'Connect Gemini before starting. Preview and saved reports need no key.':'The worst-case estimate is within the remaining budget. Actual requests and cost can vary.');text('scan-plan-error','');$('discovery-preview').showModal();}finally{setBusy(false);}});};

$('start-discovery').onclick=()=>attempt(async()=>{if(!discoveryPreview||!discoveryPreviewBody)throw Error('Preview the scan again before starting.');if(discoveryPreview.model&&discoveryPreview.model!==connection.model){invalidateDiscoveryPreview();throw Error('The connected model changed. Preview the scan again for its updated request and cost estimate.');}const body=discoveryBody();if(JSON.stringify(body)!==JSON.stringify(discoveryPreviewBody)){invalidateDiscoveryPreview();throw Error('The brief changed. Preview the scan again before starting.');}try{await job('/projects/'+project.id+'/discovery',body);}catch(e){text('scan-plan-error',e.message);throw e;}discoveryPreview=null;discoveryPreviewBody=null;$('discovery-preview').close();});


let repairDialogContext=null,repairDecisionBusy=false,repairPickerContext=null,repairQueue=[];
function repairScope(runId=null){const rid=runId||project?.discovery_view||(step===4?selectedDiscoveryRun:null);return rid?{pid:project.id,rid}:{pid:project.id};}
function scopeRun(ctx){return project?.discovery_runs?.find(r=>r.id===ctx.rid)||(project?.discovery_report?.id===ctx.rid?project.discovery_report:null);}
function scopeItems(ctx){if(ctx.rid){const run=scopeRun(ctx);return (run?.episode_results||[]).map(row=>({id:row.episode_id,title:'Episode '+(row.number??row.episode_number??'?'),status:row.status,current:row.draft,proposal:run.repair_proposals?.[row.episode_id]}));}return (project.groups||[]).flatMap(g=>(g.variations||[]).slice(0,1).map(v=>({id:v.id,title:g.title||v.angle||'Promo',current:v,proposal:project.proposals?.[v.id]})));}
function failedItems(ctx){return scopeItems(ctx).filter(row=>row.current?row.current.validation?.eligible===false:row.status==='needs_repair');}
function repairSummary(area,v){area.replaceChildren();if(!v){area.append(node('p','No usable source draft was returned.'));return;}const valid=v.validation||{};area.append(node('strong',`${valid.duration??'?'}s · ${valid.eligible?'Cut checks passed':'Needs repair'}`,'repair-status'));(Array.isArray(v.segments)?v.segments:[]).filter(s=>s&&typeof s==='object').forEach(s=>{const ep=project.episodes.find(e=>e.id===s.episode_id);area.append(node('p',`${ep?.title||'Episode'} · ${stamp(s.start)}–${stamp(s.end)}`,'source-meta'));});const failed=(valid.checks||[]).filter(c=>!c.passed);if(failed.length){const list=node('ul',null,'repair-failures');failed.forEach(c=>list.append(node('li',`${c.name}: ${c.detail}`)));area.append(list);}const evidence=node('div',null,'repair-evidence');showEvidence(evidence,valid);area.append(evidence,node('h4','Unanswered question'),node('p',v.question||'Not provided.'));}
async function refreshRepairProject(ctx){if(project?.id!==ctx.pid)return;project=await api('/projects/'+ctx.pid);if(ctx.rid){const view=await api('/projects/'+ctx.pid+'/discovery/'+ctx.rid+'/view');project=view;selectedDiscoveryRun=ctx.rid;}if(ctx.vid){const index=(project.groups||[]).findIndex(g=>ctx.rid?(g.episode_id||g.source_ids?.[0])===ctx.vid:(g.variations||[]).some(v=>v.id===ctx.vid));if(index>=0)groupIndex=index;}render();}
function openRepairComparison(vid,context=null){const ctx=context||repairScope();const row=scopeItems(ctx).find(row=>row.id===vid);if(!row?.proposal)return;const proposal=row.proposal;repairDialogContext={...ctx,vid,current:row.current};$('repair-dialog-error').hidden=true;$('repair-keep-warning').hidden=true;text('repair-dialog-title',`Compare repair · ${row.title}`);text('repair-dialog-description',`Your current draft stays saved until you decide.${repairQueue.length?' '+repairQueue.length+' more saved repairs await a decision.':''}`);repairSummary($('repair-current-summary'),row.current);repairSummary($('repair-new-summary'),proposal.variation);$('repair-change').replaceChildren(node('p',proposal.change||proposal.message||'A proposed source recut.'),node('p','Tradeoff: '+(proposal.tradeoff||'Review the source differences before applying.'),'small'));$('repair-use-new').disabled=!proposal.variation;text('repair-apply-help',proposal.variation?.validation?.eligible?'The proposed draft passes cut checks. Keep it to replace the current draft.':proposal.variation?'The proposed cut has warnings. You can keep either draft and run Virality Review on your choice.':'No new draft was returned. You can keep and review your current draft.');if(!$('repair-dialog').open)$('repair-dialog').showModal();}
function nextRepairComparison(){const next=repairQueue.shift();if(next)openRepairComparison(next.id,next.context);else $('repair-dialog').close();}
function reviewPendingRepairs(ctx=repairScope()){repairQueue=scopeItems(ctx).filter(row=>row.proposal).map(row=>({id:row.id,context:ctx}));nextRepairComparison();}
async function repairDecision(keepNew){if(repairDecisionBusy||!repairDialogContext)return;const ctx=repairDialogContext;if(project?.id!==ctx.pid){$('repair-dialog').close();return;}repairDecisionBusy=true;const controls=[...$('repair-dialog').querySelectorAll('button')],states=controls.map(b=>b.disabled);controls.forEach(b=>b.disabled=true);$('repair-dialog-error').hidden=true;try{if(ctx.rid){await api('/projects/'+ctx.pid+'/discovery/'+ctx.rid+'/episodes/'+encodeURIComponent(ctx.vid)+'/repair-decision',{keep_new:keepNew});}else await api('/projects/'+ctx.pid+(keepNew?'/apply':'/discard'),{variation_id:ctx.vid});await refreshRepairProject(ctx);$('repair-dialog').close();nextRepairComparison();}catch(e){text('repair-dialog-error',e.message||'Your choice could not be saved.');$('repair-dialog-error').hidden=false;}finally{repairDecisionBusy=false;controls.forEach((b,i)=>b.disabled=states[i]);if(repairDialogContext){const row=scopeItems(repairDialogContext).find(row=>row.id===repairDialogContext.vid);$('repair-use-new').disabled=!row?.proposal?.variation;}}}
$('repair-use-new').onclick=()=>repairDecision(true);
$('repair-keep-current').onclick=()=>repairDecision(false);
$('repair-confirm-keep').onclick=()=>repairDecision(false);$('repair-back').onclick=()=>{$('repair-keep-warning').hidden=true;};$('repair-decide-later').onclick=()=>{if(!repairDecisionBusy)$('repair-dialog').close();};$('repair-dialog').addEventListener('cancel',e=>{if(repairDecisionBusy)e.preventDefault();});
function pickerSelection(){const boxes=[...$('repair-picker-list').querySelectorAll('input:checked')];$('repair-selected').disabled=!boxes.length;text('repair-selected',`Repair ${boxes.length||'selected'} promo${boxes.length===1?'':'s'}`);}
function openRepairPicker(runId=null,preselect=null){if(busy||!project||project.saved_output_view)return;const ctx=repairScope(runId),rows=failedItems(ctx);repairPickerContext=ctx;clear('repair-picker-list');text('repair-picker-error','');$('repair-pick-all').checked=false;rows.forEach(row=>{const label=node('label',null,'repair-picker-row'),input=document.createElement('input');input.type='checkbox';input.value=row.id;input.checked=preselect===row.id;input.onchange=pickerSelection;const copy=node('div');copy.append(node('strong',row.title));const failed=row.current?.validation?.checks?.filter(c=>!c.passed).map(c=>c.name)||[];copy.append(node('p',failed.join(' · ')||'No usable source draft','small'));label.append(input,copy);$('repair-picker-list').append(label);});if(!rows.length)$('repair-picker-list').append(node('p','No promos fail the current cut checks.'));pickerSelection();$('repair-picker').showModal();}
$('repair-pick-all').onchange=e=>{$('repair-picker-list').querySelectorAll('input').forEach(input=>input.checked=e.target.checked);pickerSelection();};$('repair-picker-close').onclick=()=>$('repair-picker').close();
$('repair-selected').onclick=()=>attempt(async()=>{const ctx=repairPickerContext,ids=[...$('repair-picker-list').querySelectorAll('input:checked')].map(input=>input.value);if(!ctx||!ids.length)return;$('repair-picker').close();try{await job('/projects/'+ctx.pid+(ctx.rid?'/discovery/'+ctx.rid+'/repairs':'/repairs'),ctx.rid?{episode_ids:ids}:{variation_ids:ids});}finally{await refreshRepairProject(ctx);repairQueue=scopeItems(ctx).filter(row=>ids.includes(row.id)&&row.proposal).map(row=>({id:row.id,context:ctx}));nextRepairComparison();}});
$('repair-current').onclick=()=>{const ctx=repairScope(),g=project.groups?.[groupIndex],id=ctx.rid?(g?.episode_id||g?.source_ids?.[0]):activeVariation()?.id;openRepairPicker(ctx.rid,id);};$('repair-failed').onclick=()=>openRepairPicker();$('review-pending-repairs').onclick=()=>reviewPendingRepairs();
$('run-virality-review').onclick=()=>attempt(async()=>{const ctx=repairScope(),g=project.groups[groupIndex];await job('/projects/'+ctx.pid+(ctx.rid?'/discovery/'+ctx.rid+'/episodes/'+encodeURIComponent(g.episode_id||g.source_ids[0])+'/virality-review':'/virality-review'),ctx.rid?{}:{variation_id:activeVariation().id});});


$('close-discovery-preview').onclick=()=>$('discovery-preview').close();

let reviewPickerContext=null,reviewBatchRunning=false;
function reviewPickerSelection(){const boxes=[...$('review-picker-list').querySelectorAll('input')],count=boxes.filter(b=>b.checked).length;$('review-selected').disabled=!count;text('review-selected',`Review ${count||'selected'} promo${count===1?'':'s'}`);$('review-pick-all').checked=!!boxes.length&&count===boxes.length;$('review-pick-all').indeterminate=count>0&&count<boxes.length;}
function openReviewPicker(runId=null){if(busy||reviewBatchRunning||!project||project.saved_output_view)return;const ctx=repairScope(runId);reviewPickerContext=ctx;clear('review-picker-list');text('review-picker-error','');scopeItems(ctx).filter(row=>row.current).forEach(row=>{const label=node('label',null,'repair-picker-row'),input=document.createElement('input');input.type='checkbox';input.value=row.id;input.onchange=reviewPickerSelection;const copy=node('div'),score=discoveryRating(row.current);copy.append(node('strong',row.title),node('p',`${score===null?'Not rated':score+'/100 · Review again'} · ${row.current.validation?.eligible?'Cut checks passed':'Cut-check warnings'}${row.proposal?' · Repair decision pending':''}`,'small'));label.append(input,copy);$('review-picker-list').append(label);});reviewPickerSelection();$('review-picker').showModal();}
$('review-many').onclick=()=>openReviewPicker();
$('review-picker-close').onclick=()=>$('review-picker').close();
$('review-pick-all').onchange=e=>{$('review-picker-list').querySelectorAll('input').forEach(input=>input.checked=e.target.checked);reviewPickerSelection();};
$('review-selected').onclick=()=>attempt(async()=>{const ctx=reviewPickerContext,ids=[...$('review-picker-list').querySelectorAll('input:checked')].map(input=>input.value);if(!ctx||!ids.length||reviewBatchRunning)return;$('review-picker').close();reviewBatchRunning=true;$('review-batch-notice').hidden=false;let done=0;try{for(const id of ids){if(project?.id!==ctx.pid)break;text('review-batch-notice',`Reviewing promo ${done+1} of ${ids.length} · Completed scores are saved individually.`);const result=await job('/projects/'+ctx.pid+(ctx.rid?'/discovery/'+ctx.rid+'/episodes/'+encodeURIComponent(id)+'/virality-review':'/virality-review'),ctx.rid?{}:{variation_id:id});if(result?.status!=='done')break;done++;} }finally{reviewBatchRunning=false;await refreshRepairProject(ctx);text('review-batch-notice',`${done} of ${ids.length} selected reviews completed. Saved scores are preserved.`);}});
