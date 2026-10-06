const task=await useOrCreateTaskSpace('TalentPilot 申请只读连接');
await openOrReuseTab('https://www.italent.cn/',{wait:true,timeout:20});
let ready=false;
for(let n=0;n<20;n++){
  ready=await js(`Boolean(document.querySelector('[data-udc*="2d0174f6-e4d5-4e84-8e3f-5e28fcfeb265"]'))`);
  if(ready)break;
  await wait(1);
}
if(!ready)throw new Error('Manual login required');
await js(`document.querySelector('[data-udc*="2d0174f6-e4d5-4e84-8e3f-5e28fcfeb265"]').click()`);
for(let n=0;n<25;n++){
  ready=await js(`(()=>{try{return document.querySelector('iframe')?.contentDocument.body.innerText.includes('全部简历')}catch{return false}})()`);
  if(ready)break;
  await wait(1);
}
if(!ready)throw new Error('Resume UI unavailable');
const initialized=await js(String.raw`(()=>{
  const w=document.querySelector('iframe').contentWindow;
  const scope=w.document.body.innerText.split('\n').slice(0,2).join(' / ');
  if(scope!=='百度社招实习生招聘流程 / 招聘中职位')return {error:'Unverified process or job filter'};
  if(!w.__talentpilotFetchOriginal)w.__talentpilotFetchOriginal=w.fetch.bind(w);
  w.__tpApplicationPages=[];
  delete w.__tpApplicationError;
  w.fetch=async function(input,options){
    const response=await w.__talentpilotFetchOriginal(input,options);
    const url=typeof input==='string'?input:input.url;
    if(url.includes('/api/v2/UI/TableList')){
      try{
        const request=JSON.parse(options?.body||'{}');
        if(request.table_data?.metaObjName==='Recruitment.Apply'){
          const data=await response.clone().json();
          const paging=data.cmp_data?.paging;
          if(!paging||!Array.isArray(data.biz_data)||!Array.isArray(data.rows))throw new Error('Unexpected response schema');
          const ids=data.rows.map(r=>r.cmp_data?.id);
          const applications=data.biz_data.map(b=>{
            const v=k=>b[k]?.value??null,t=k=>b[k]?.text??null;
            const label=t('JobId'),code=label?.match(/[（(](J\d+)[）)]/);
            return {application_id:v('_id'),candidate_id:v('ApplicantId'),candidate_name:t('ApplicantId'),
              job_id:code?.[1],job_uuid:v('JobId'),job_title:label?.replace(/[（(]J\d+[）)]/,'').trim(),
              current_stage:t('ProcessPhaseId'),current_status:t('ProcessStatusId'),entry_status:t('EntryStatus'),
              status_changed_at:t('ProcessStatusChangeDate'),source_page:paging.page+1};
          });
          if(applications.some(a=>!a.application_id||!a.candidate_id||!a.job_id||!a.job_uuid||!ids.includes(a.application_id)))
            throw new Error('Stable identity mismatch');
          w.__tpApplicationPages.push({page:paging.page,total:paging.total,capacity:paging.capacity,applications});
        }
      }catch{w.__tpApplicationError='Response schema or identity validation failed'}
    }
    return response;
  };
  const all=[...w.document.querySelectorAll('*')].find(e=>e.textContent.trim()==='全部简历');
  if(!all)return {error:'All-resumes control missing'};
  all.click();
  return {scope};
})()`);
if(initialized.error)throw new Error(initialized.error);
await wait(1);
await js(`(()=>{const d=document.querySelector('iframe').contentDocument;const first=[...d.querySelectorAll('a')].find(a=>a.textContent.trim()==='1');if(first)first.click()})()`);
let total=null,capacity=null;
const applications=new Map(),pages=[];
for(let page=0;page<150;page++){
  let result=null;
  for(let waitCount=0;waitCount<60;waitCount++){
    result=await js(`(()=>{const w=document.querySelector('iframe').contentWindow;if(w.__tpApplicationError)return {error:w.__tpApplicationError};return w.__tpApplicationPages.find(p=>p.page===${page})||null})()`);
    if(result)break;
    await wait(.2);
  }
  if(!result||result.error)throw new Error('Page unavailable or invalid; refusing partial success');
  if(total!==null&&(result.total!==total||result.capacity!==capacity))throw new Error('Paging changed during read');
  total=result.total;capacity=result.capacity;
  if(!Number.isInteger(total)||total<1||total>9000||!Number.isInteger(capacity)||capacity<1)throw new Error('Out of bounded scope');
  const expected=Math.min(capacity,total-page*capacity);
  if(result.applications.length!==expected)throw new Error('Incomplete page');
  for(const a of result.applications){
    if(applications.has(a.application_id))throw new Error('Duplicate application across pages; collection must be retried');
    applications.set(a.application_id,a);
  }
  pages.push({page:page+1,count:result.applications.length,total});
  if(applications.size===total)break;
  let clicked=false;
  for(let n=0;n<40;n++){
    await wait(.2);
    clicked=await js(`(()=>{const d=document.querySelector('iframe').contentDocument;const next=[...d.querySelectorAll('a')].find(a=>a.textContent.trim()==='下一页');if(next)next.click();return !!next})()`);
    if(clicked)break;
  }
  if(!clicked)throw new Error('Next-page control missing');
}
await js(`(()=>{const w=document.querySelector('iframe').contentWindow;if(w.__talentpilotFetchOriginal)w.fetch=w.__talentpilotFetchOriginal;delete w.__tpApplicationPages})()`);
if(applications.size!==total)throw new Error('Incomplete application inventory');
cliLog('TALENTPILOT_RESULT:'+JSON.stringify({state:'verified_application_inventory',scope:initialized.scope+' / 全部简历',
  declared:total,captured:applications.size,pages,applications:[...applications.values()],
  interviews_refreshed:false,offers_refreshed:false}));
