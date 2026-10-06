const task=await useOrCreateTaskSpace('TalentPilot 面试只读连接');
await openOrReuseTab('https://www.italent.cn/',{wait:true,timeout:20});
let ready=false;
let openedInterviewPage=false;
const existingTabs=await listTabs();
const existingInterviewTab=existingTabs.find(tab=>tab.title==='面试');
if(existingInterviewTab){
  await switchTab(existingInterviewTab.targetId);
  openedInterviewPage=true;
}
let restoredFetch=false;
try{
  restoredFetch=await js(`(()=>{try{const w=document.querySelector('iframe')?.contentWindow;if(!w?.__tpInterviewOriginal)return false;w.fetch=w.__tpInterviewOriginal;delete w.__tpInterviewOriginal;delete w.__tpInterviewPages;delete w.__tpInterviewError;return true}catch{return false}})()`);
}catch{}
if(restoredFetch)await wait(3);
for(let n=0;n<20;n++){
  ready=await js(`(()=>{try{return document.querySelector('iframe')?.contentDocument.body.innerText.includes('全部时间面试')}catch{return false}})()`);
  if(ready)break;
  if(!openedInterviewPage){
    const menuReady=await js(`Boolean(document.querySelector('[data-udc*="191e6e73-cbf9-411f-8125-118cf2b93599"]'))`);
    if(menuReady){
      await js(`document.querySelector('[data-udc*="191e6e73-cbf9-411f-8125-118cf2b93599"]').click()`);
      openedInterviewPage=true;
    }
  }
  await wait(1);
}
if(!ready)throw new Error('Interview UI unavailable');
await wait(3);
await js(`(()=>{
  const d=document.querySelector('iframe').contentDocument;
  const tab=[...d.querySelectorAll('[role="tab"]')].find(e=>e.textContent.trim()==='今天及以后面试');
  if(!tab)throw new Error('Upcoming tab missing');
  tab.click();
})()`);
await wait(2);
await js(String.raw`(()=>{
  const w=document.querySelector('iframe').contentWindow;
  if(!w.__tpInterviewOriginal)w.__tpInterviewOriginal=w.fetch.bind(w);
  w.__tpInterviewPages=[];delete w.__tpInterviewError;
  w.fetch=async function(input,options){
    const response=await w.__tpInterviewOriginal(input,options);
    const url=typeof input==='string'?input:input.url;
    if(url.includes('/api/v2/UI/TableList')){
      try{
        const request=JSON.parse(options?.body||'{}');
        if(request.table_data?.metaObjName==='Recruitment.InterviewArrangement'){
          const data=await response.clone().json(),paging=data.cmp_data?.paging;
          if(!paging||!Array.isArray(data.biz_data))throw new Error('Schema');
          const interviews=data.biz_data.map(b=>{
            const v=k=>b[k]?.value??null,t=k=>b[k]?.text??null;
            const applicants=JSON.parse(v('Applicants')||'[]'),label=t('JobIds');
            const codes=[...label.matchAll(/[（(](J\d+)[）)]/g)].map(m=>m[1]);
            if(applicants.length!==1||codes.length!==1)throw new Error('Group mapping requires review');
            const a=applicants[0],summary=JSON.parse(v('InterviewerIds')||'{}');
            const evaluations=JSON.parse(t('EvaluationResult')||'[]').map(e=>typeof e==='string'?JSON.parse(e):e);
            const names=(b.InterviewerIds.textlist||[]).map(e=>JSON.parse(e));
            const people=names.map(p=>{
              const evaluation=evaluations.find(e=>String(e.InterviewerId)===String(p.value));
              return {interviewer_id:String(p.value),name:String(p.text).replace(/\([^)]*@[^)]*\)/g,''),
                result:evaluation?.Evaluation||null,feedback_collected:false};
            });
            return {interview_id:v('_id'),application_id:a.applyId,candidate_id:a.applicantId,
              candidate_name:a.name,job_id:codes[0],job_label:label,round:t('InterviewTypeId'),
              scheduled_at:t('InterviewStartTime'),status:t('Status'),overall_result:summary.conclusion||null,
              organizer:t('CreatedBy'),interviewers:people,source_page:paging.page+1};
          });
          if(interviews.some(i=>!i.interview_id||!i.application_id||!i.candidate_id))throw new Error('Identity');
          w.__tpInterviewPages.push({page:paging.page,total:paging.total,capacity:paging.capacity,interviews});
        }
      }catch{w.__tpInterviewError='Interview response schema or identity validation failed'}
    }
    return response;
  };
  const tab=[...w.document.querySelectorAll('[role="tab"]')].find(e=>e.textContent.trim()==='全部时间面试');
  if(!tab)throw new Error('All-time tab missing');
  tab.click();
})()`);
for(let n=0;n<40;n++){
  ready=await js(`(()=>{const d=document.querySelector('iframe').contentDocument;return d.body.innerText.includes('共')&&[...d.querySelectorAll('a')].some(a=>a.textContent.trim()==='下一页')})()`);
  if(ready)break;
  await wait(.5);
}
if(!ready)throw new Error('Interview list did not load');
async function clickPage(label) {
  const point=await js(`(()=>{
    const f=document.querySelector('iframe'),d=f.contentDocument;
    const all=[...d.querySelectorAll('a')].filter(a=>a.textContent.trim()===${JSON.stringify(label)});
    const e=all.find(a=>{const r=a.getBoundingClientRect();return r.width>0&&r.height>0})||all[0];
    if(!e)return null;
    const a=e.getBoundingClientRect(),b=f.getBoundingClientRect();
    return {x:a.x+b.x+a.width/2,y:a.y+b.y+a.height/2};
  })()`);
  if(point)await click(point);
  return !!point;
}
async function currentPage() {
  return await js(`(()=>{
    const d=document.querySelector('iframe').contentDocument;
    const e=[...d.querySelectorAll('a')].find(a=>a.classList.contains('phoenix-pagination__numberButtonPure--clicked'));
    return e?Number(e.textContent.trim())-1:null;
  })()`);
}
async function waitForPage(page) {
  for(let n=0;n<60;n++){
    const result=await js(`(()=>{const w=document.querySelector('iframe').contentWindow;if(w.__tpInterviewError)return {error:w.__tpInterviewError};return w.__tpInterviewPages.find(p=>p.page===${page})||null})()`);
    if(result)return result;
    await wait(.2);
  }
  return null;
}
async function loadPage(page) {
  if(page>0&&!await clickPage(String(page+1)))await clickPage('下一页');
  let result=await waitForPage(page);
  if(result)return result;
  const alternate=page===0?1:Math.max(1,page);
  if(await clickPage(String(alternate+1))){
    await waitForPage(alternate);
    if(await clickPage(String(page+1)))result=await waitForPage(page);
  }
  return result;
}
let total=null,capacity=null;
const interviews=new Map(),pages=[];
for(let page=0;page<100;page++){
  let result=await waitForPage(page);
  if(!result)result=await loadPage(page);
  if(!result||result.error)throw new Error('Invalid interview page');
  if(total!==null&&(total!==result.total||capacity!==result.capacity))throw new Error('Interview paging changed');
  total=result.total;capacity=result.capacity;
  if(!Number.isInteger(total)||total<1||total>6000||!Number.isInteger(capacity)||capacity<1)throw new Error('Out of bounded scope');
  if(result.interviews.length!==Math.min(capacity,total-page*capacity))throw new Error('Incomplete interview page');
  for(const row of result.interviews){
    if(interviews.has(row.interview_id))throw new Error('Duplicate interview; retry snapshot');
    interviews.set(row.interview_id,row);
  }
  pages.push({page:page+1,count:result.interviews.length,total});
  if(interviews.size===total)break;
  let clicked=false;
  for(let n=0;n<40;n++){
    await wait(.2);
    clicked=await clickPage('下一页');
    if(clicked)break;
  }
  if(!clicked)throw new Error('Next page unavailable');
}
await js(`(()=>{const w=document.querySelector('iframe').contentWindow;if(w.__tpInterviewOriginal)w.fetch=w.__tpInterviewOriginal;delete w.__tpInterviewOriginal;delete w.__tpInterviewPages;delete w.__tpInterviewError})()`);
if(interviews.size!==total)throw new Error('Incomplete interviews');
cliLog('TALENTPILOT_RESULT:'+JSON.stringify({state:'verified_interview_inventory',declared:total,captured:interviews.size,
  scope:'全部时间面试 / 当前账号可见列表',pages,interviews:[...interviews.values()],feedback_refreshed:false}));
