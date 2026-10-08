const task = await useOrCreateTaskSpace('TalentPilot iTalent 推荐执行');
await openOrReuseTab('https://www.italent.cn/', {wait: true, timeout: 25});

const compact = value => String(value || '').replace(/\s/g, '');
const target = TALENTPILOT_INPUT.target;
const targetTitle = `${target.title}(${target.job_id})`;
const jobHistory = async applicantId => {
  const url = `https://cloud.italent.cn/mrest/Recruitment/TalentPoolV6/100000/ITalentDetailProvider/GetJoblistOfApplicant?applicantId=${encodeURIComponent(applicantId)}&type=0`;
  const raw = await browserFetch(url, {method: 'GET'});
  const body = JSON.parse(raw);
  return Array.isArray(body?.Data?.JobList) ? body.Data.JobList : [];
};
const results = [];
for (const candidate of TALENTPILOT_INPUT.candidates) {
  try {
    const detail = `https://cloud.italent.cn/PageHome/Index?applicantId=${encodeURIComponent(candidate.applicant_id)}&fromPage=RecruitTalentPool&isBatch=false&isBusinessScene=true&product=Recruitment&keyName=Nusion&pageCode=TalentDetail&appCode=Recruitment#/viewDynamic?t=t`;
    await gotoAndWait(detail, {timeout:30, settle:2});
    let jobs = await jobHistory(candidate.applicant_id);
    if (jobs.some(job => compact(job.Title) === compact(targetTitle))) {
      results.push({applicant_id:candidate.applicant_id,name:candidate.name,status:'already_at_target',read_back:true});
      continue;
    }
    const opened = await js(String.raw`(() => {const e=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.innerText||'').trim()==='推荐到职位'&&(e.offsetWidth||e.offsetHeight));if(!e)return false;e.click();return true})()`);
    await wait(1);
    const picker = await js(String.raw`(() => {const e=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.innerText||'').trim()==='请选择职位'&&(e.offsetWidth||e.offsetHeight));if(!e)return false;e.parentElement?.click();e.click();return true})()`);
    await wait(.5);
    if(!opened||!picker) throw new Error('推荐表单不可用');
    let searchInput='input[placeholder="搜索职位名称/编号/需求部门"]';
    const legacySelect=await js(String.raw`(()=>{const label=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.innerText||'').trim()==='请选择职位'&&(e.offsetWidth||e.offsetHeight));const input=label?.parentElement?.querySelector('input:not(:disabled)');if(!input)return false;input.setAttribute('data-talentpilot-job-search','true');return true})()`);
    if(legacySelect){searchInput='input[data-talentpilot-job-search="true"]';await js(String.raw`(()=>{const input=document.querySelector('input[data-talentpilot-job-search]');input?.parentElement?.parentElement?.click();input?.focus()})()`);for(let n=0;n<25;n++){if(await js(String.raw`Boolean(document.querySelector('input[placeholder="搜索职位名称/编号/需求部门"]'))`))break;await wait(.2)}searchInput='input[placeholder="搜索职位名称/编号/需求部门"]';if(!await js(String.raw`Boolean(document.querySelector('input[placeholder="搜索职位名称/编号/需求部门"]'))`))throw new Error('职位搜索框未加载');await fillInput(searchInput,target.job_id);try{await pressKey('ENTER')}catch{}}else{await fillInput(searchInput,target.job_id)}
    await wait(.5);
    await wait(1.5);
    const selected = await js(`(() => {const title=${JSON.stringify(targetTitle)},dept=${JSON.stringify(target.department)};const items=[...document.querySelectorAll('.recruit-paas-recommend-form-lookup__item')].filter(e=>e.offsetWidth||e.offsetHeight);const matches=items.filter(e=>e.innerText.replace(/\s/g,'').includes(title.replace(/\s/g,''))&&e.innerText.includes(dept));if(matches.length!==1)return false;matches[0].click();return true})()`);
    await wait(.8);
    await js(String.raw`(()=>{const inputs=[...document.querySelectorAll('input')].filter(e=>e.offsetWidth||e.offsetHeight);const input=inputs.find(e=>String(e.value||'').startsWith('简历')&&String(e.value||'').includes('/'))||inputs.find(e=>e.value==='');const parent=input&&input.parentElement&&input.parentElement.parentElement;if(parent)parent.click();return !!input})()`);
    await wait(.4);
    const phase=target.stage.split('/')[0],status=target.stage.split('/')[1];
    await js(`(()=>{const phase=${JSON.stringify(phase)};const items=[...document.querySelectorAll('.cascader_item_text')].filter(e=>(e.innerText||'').trim()===phase&&(e.offsetWidth||e.offsetHeight));if(items.length!==1)return false;items[0].click();return true})()`);
    await wait(.3);
    await js(`(()=>{const status=${JSON.stringify(status)};const items=[...document.querySelectorAll('.cascader_item_text')].filter(e=>(e.innerText||'').trim()===status&&(e.offsetWidth||e.offsetHeight));if(items.length!==1)return false;items[0].click();return true})()`);
    await wait(.4);
    const fields=await js(String.raw`(() => [...document.querySelectorAll('input')].filter(e=>e.offsetWidth||e.offsetHeight).map(e=>e.value).filter(Boolean))()`);
    if(!selected||!fields.some(value=>compact(value)===compact(targetTitle))||!fields.some(value=>compact(value)===compact(target.stage))) throw new Error('目标岗位或阶段复核失败');
    const submitted=await js(String.raw`(() => {const drawers=[...document.querySelectorAll('.phoenix-drawer')].filter(e=>e.offsetWidth||e.offsetHeight);const scope=drawers[drawers.length-1];if(!scope)return false;const buttons=[...scope.querySelectorAll('*')].filter(e=>e.children.length===0&&(e.innerText||'').trim()==='确定'&&(e.offsetWidth||e.offsetHeight));const button=buttons[buttons.length-1];if(!button)return false;button.click();return true})()`);
    if(!submitted) throw new Error('未找到最终确定按钮');
    await wait(3);
    jobs=await jobHistory(candidate.applicant_id);
    const verified=jobs.some(job=>compact(job.Title)===compact(targetTitle));
    results.push({applicant_id:candidate.applicant_id,name:candidate.name,status:verified?'recommended_verified':'uncertain',read_back:verified});
    if(!verified) break;
  } catch(error) {
    results.push({applicant_id:candidate.applicant_id,name:candidate.name,status:'uncertain',read_back:false,reason:String(error).slice(0,240)});
    break;
  }
}
cliLog('TALENTPILOT_RESULT:'+JSON.stringify({state:'completed',results}));
