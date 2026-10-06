const task=await useOrCreateTaskSpace('TalentPilot 候选人历史只读匹配');
await openOrReuseTab('https://www.italent.cn/',{wait:true,timeout:20});
let ready=false;
for(let n=0;n<20;n++){
  ready=await js(`Boolean([...document.querySelectorAll('.menu-name')].find(e=>e.textContent.trim()==='候选人信息查询'))`);
  if(ready)break;
  await wait(1);
}
if(!ready)throw new Error('Manual login required');
await js(`(()=>{const e=[...document.querySelectorAll('.menu-name')].find(e=>e.textContent.trim()==='候选人信息查询');e.click();return true})()`);
let target=null;
for(let n=0;n<30;n++){
  const targets=await cdp('Target.getTargets');
  target=targets.targetInfos.find(t=>t.type==='iframe'&&t.url.includes('baidurecruit.tms.beisen.com/DataIsolation/Enter'));
  if(target)break;
  await wait(1);
}
if(!target)throw new Error('Candidate lookup UI unavailable');
const attached=await cdp('Target.attachToTarget',{targetId:target.targetId,flatten:true});
const sid=attached.sessionId;
const payload=JSON.stringify(TALENTPILOT_INPUT);
const expression=`(()=>{
  const input=${payload};
  window.__tpHistoryDone=false;window.__tpHistoryError=null;window.__tpHistoryResult=null;
  const value=(row,...keys)=>{for(const key of keys)if(row&&row[key]!=null&&row[key]!=="")return row[key];return null};
  const query=async(params)=>{
    const token=new URL(location.href).searchParams.get('data');
    if(!token)throw new Error('Candidate query token missing');
    const url='/DataIsolation/GetApplyInfo?'+new URLSearchParams({data:token,...params});
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),30000);
    try{const response=await fetch(url,{method:'GET',signal:controller.signal});if(!response.ok)throw new Error('Candidate query failed');const body=await response.json();return Array.isArray(body.data)?body.data:body.data?[body.data]:[]}finally{clearTimeout(timer)}
  };
  (async()=>{
    const results=[];
    for(const identity of input){
      let rows=[],basis=null,lastAttempt=null;
      for(const [key,val] of [['email',identity.email],['mobile',identity.mobile],['name',identity.name]]){
        if(!val)continue;
        lastAttempt=key;rows=await query({[key]:val});if(rows.length){basis=key;break}
      }
      const applications=rows.map(row=>{
        const state=String(value(row,'state','stageStatus','processStatus')||'');
        const hired=/(录用|入职)/.test(state),rejected=/淘汰/.test(state);
        return {
          candidate_id:String(value(row,'applicantId','candidateId','id')||''),
          candidate_name:value(row,'name','candidateName'),job_title:value(row,'jobName','jobTitle'),
          job_id:(String(value(row,'jobName','jobTitle')||'').match(/J\d+/)||[])[0]||null,
          department:value(row,'dept','department'),stage_status:state||null,
          applied_at:value(row,'lastSubmissionDate','submissionDate','appliedAt'),
          rejected_stage:rejected?state.split(/[-/\s]/).filter(Boolean)[0]:null,
          outcome:rejected?'rejected':hired?'hired':'in_process_or_other',
          hired_at:value(row,'hireDate','hiredAt','entryDate','onboardDate'),
          status_changed_at:value(row,'modifiedTime','statusChangedAt'),
          locked:value(row,'locked','isLocked','applicantLocked')===true
        };
      });
      const names=new Set(applications.map(r=>r.candidate_name).filter(Boolean));
      results.push({file_id:identity.file_id,match_basis:basis,candidate_name:applications[0]?.candidate_name||identity.name,
        searched_by:rows.length?basis:lastAttempt,
        name_conflict:identity.name&&names.size>0&&![...names].some(n=>String(n).replace(/\s/g,'').toLowerCase()===String(identity.name).replace(/\s/g,'').toLowerCase()),applications});
    }
    window.__tpHistoryResult={results};window.__tpHistoryDone=true;
  })().catch(error=>{window.__tpHistoryError=String(error);window.__tpHistoryDone=true});
  return true;
})()`;
await cdp('Runtime.evaluate',{expression,returnByValue:true},sid);
let finished=null;
for(let n=0;n<600;n++){
  const polled=await cdp('Runtime.evaluate',{expression:`({done:window.__tpHistoryDone,error:window.__tpHistoryError,result:window.__tpHistoryResult})`,returnByValue:true},sid);
  finished=polled.result.value;if(finished.done)break;await wait(.5);
}
if(!finished?.done||finished.error)throw new Error(finished?.error||'Candidate history query timed out');
cliLog('TALENTPILOT_RESULT:'+JSON.stringify({state:'verified_candidate_history',...finished.result}));
