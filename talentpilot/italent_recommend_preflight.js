const task = await useOrCreateTaskSpace('TalentPilot iTalent 推荐预检');
await openOrReuseTab('https://www.italent.cn/', {wait: true, timeout: 25});

const compact = value => String(value || '').replace(/\s/g, '');
const targetTitle = `${TALENTPILOT_INPUT.job.title}(${TALENTPILOT_INPUT.job.job_id})`;
const jobHistory = async applicantId => {
  const url = `https://cloud.italent.cn/mrest/Recruitment/TalentPoolV6/100000/ITalentDetailProvider/GetJoblistOfApplicant?applicantId=${encodeURIComponent(applicantId)}&type=0`;
  const raw = await browserFetch(url, {method: 'GET'});
  const body = JSON.parse(raw);
  return Array.isArray(body?.Data?.JobList) ? body.Data.JobList : [];
};
const closeDrawer = async () => {
  await js(String.raw`(() => {
    const drawers=[...document.querySelectorAll('.phoenix-drawer')].filter(e=>e.offsetWidth||e.offsetHeight);
    const scope=drawers[drawers.length-1];
    const button=scope?.querySelector('button[aria-label="Close"]') || document.querySelector('button[aria-label="Close"]');
    if(button) button.click();
  })()`);
  await wait(.5);
};

const results = [];
let verifiedTarget = null;
for (const candidate of TALENTPILOT_INPUT.candidates) {
  const detail = `https://cloud.italent.cn/PageHome/Index?applicantId=${encodeURIComponent(candidate.applicant_id)}&fromPage=RecruitTalentPool&isBatch=false&isBusinessScene=true&product=Recruitment&keyName=Nusion&pageCode=TalentDetail&appCode=Recruitment#/viewDynamic?t=t`;
  await gotoAndWait(detail, {timeout: 30, settle: 2});
  const existing = await jobHistory(candidate.applicant_id);
  if (existing.some(job => compact(job.Title) === compact(targetTitle))) {
    results.push({...candidate, status: 'already_at_target', existing_jobs: existing.map(job => job.Title).filter(Boolean)});
    continue;
  }
  const opened = await js(String.raw`(() => {
    const e=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.innerText||'').trim()==='推荐到职位'&&(e.offsetWidth||e.offsetHeight));
    if(!e) return false; e.click(); return true;
  })()`);
  await wait(1);
  const picker = await js(String.raw`(() => {
    const e=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.innerText||'').trim()==='请选择职位'&&(e.offsetWidth||e.offsetHeight));
    if(!e) return false; e.parentElement?.click(); e.click(); return true;
  })()`);
  await wait(.5);
  if (!opened || !picker) {
    results.push({...candidate, status: 'blocked', reason: '推荐表单不可用', existing_jobs: existing.map(job => job.Title).filter(Boolean)});
    await closeDrawer();
    continue;
  }
  let searchInput = 'input[placeholder="搜索职位名称/编号/需求部门"]';
  const legacySelect = await js(String.raw`(() => {
    const label=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.innerText||'').trim()==='请选择职位'&&(e.offsetWidth||e.offsetHeight));
    const input=label?.parentElement?.querySelector('input:not(:disabled)');
    if(!input) return false; input.setAttribute('data-talentpilot-job-search','true'); return true;
  })()`);
  if (legacySelect) {
    searchInput = 'input[data-talentpilot-job-search="true"]';
    await js(String.raw`(() => {const input=document.querySelector('input[data-talentpilot-job-search]');input?.parentElement?.parentElement?.click();input?.focus()})()`);
    for (let n=0;n<25;n++) {
      if (await js(String.raw`Boolean(document.querySelector('input[placeholder="搜索职位名称/编号/需求部门"]'))`)) break;
      await wait(.2);
    }
    searchInput = 'input[placeholder="搜索职位名称/编号/需求部门"]';
    if (!await js(String.raw`Boolean(document.querySelector('input[placeholder="搜索职位名称/编号/需求部门"]'))`)) {
      results.push({...candidate,status:'blocked',reason:'职位搜索框未加载',existing_jobs:existing.map(job=>job.Title).filter(Boolean)});
      await closeDrawer();
      continue;
    }
    await fillInput(searchInput, TALENTPILOT_INPUT.job.job_id);
    try { await pressKey('ENTER'); } catch {}
  } else {
    await fillInput(searchInput, TALENTPILOT_INPUT.job.job_id);
  }
  await wait(.5);
  await wait(1.5);
  const selected = await js(`(() => {
    const title=${JSON.stringify(targetTitle)}, dept=${JSON.stringify(TALENTPILOT_INPUT.job.department)};
    const items=[...document.querySelectorAll('.recruit-paas-recommend-form-lookup__item')].filter(e=>e.offsetWidth||e.offsetHeight);
    const matches=items.filter(e=>e.innerText.replace(/\s/g,'').includes(title.replace(/\s/g,''))&&e.innerText.includes(dept));
    if(matches.length!==1) return {ok:false,count:matches.length};
    matches[0].click(); return {ok:true,text:matches[0].innerText};
  })()`);
  await wait(.8);
  await js(`(() => {
    const target=${JSON.stringify(TALENTPILOT_INPUT.target_stage)};
    const inputs=[...document.querySelectorAll('input')].filter(e=>e.offsetWidth||e.offsetHeight);
    const stageInput=inputs.find(e=>String(e.value||'').startsWith('简历')&&String(e.value||'').includes('/')) || inputs.find(e=>e.value==='');
    if(!stageInput)return false;
    if(stageInput.value===target)return true;
    const parent=stageInput.parentElement&&stageInput.parentElement.parentElement;
    if(parent)parent.click();
    return true;
  })()`);
  await wait(.4);
  const phase = TALENTPILOT_INPUT.target_stage.split('/')[0];
  const status = TALENTPILOT_INPUT.target_stage.split('/')[1];
  await js(`(() => {const phase=${JSON.stringify(phase)};const items=[...document.querySelectorAll('.cascader_item_text')].filter(e=>(e.innerText||'').trim()===phase&&(e.offsetWidth||e.offsetHeight));if(items.length!==1)return false;items[0].click();return true})()`);
  await wait(.3);
  await js(`(() => {const status=${JSON.stringify(status)};const items=[...document.querySelectorAll('.cascader_item_text')].filter(e=>(e.innerText||'').trim()===status&&(e.offsetWidth||e.offsetHeight));if(items.length!==1)return false;items[0].click();return true})()`);
  await wait(.4);
  const fields = await js(String.raw`(() => [...document.querySelectorAll('input')].filter(e=>e.offsetWidth||e.offsetHeight).map(e=>e.value).filter(Boolean))()`);
  const titleOk = fields.some(value => compact(value) === compact(targetTitle));
  const stageOk = fields.some(value => compact(value) === compact(TALENTPILOT_INPUT.target_stage));
  if (!selected.ok || !titleOk || !stageOk) {
    results.push({...candidate, status: 'blocked', reason: '岗位名称、编号、部门或阶段未精确匹配', existing_jobs: existing.map(job => job.Title).filter(Boolean)});
    await closeDrawer();
    continue;
  }
  verifiedTarget = {job_id: TALENTPILOT_INPUT.job.job_id, title: TALENTPILOT_INPUT.job.title,
    department: TALENTPILOT_INPUT.job.department, stage: TALENTPILOT_INPUT.target_stage};
  results.push({...candidate, status: 'eligible', existing_jobs: existing.map(job => job.Title).filter(Boolean)});
  await closeDrawer();
}
const eligible = results.filter(item => item.status === 'eligible');
const blocked = results.filter(item => item.status !== 'eligible');
cliLog('TALENTPILOT_RESULT:' + JSON.stringify({
  state: eligible.length === TALENTPILOT_INPUT.candidates.length ? 'ready_for_confirmation' : 'blocked',
  reason: blocked.length ? '存在已在目标岗位或未通过实时预检的候选人' : '',
  target: verifiedTarget || {job_id: TALENTPILOT_INPUT.job.job_id, title: TALENTPILOT_INPUT.job.title,
    department: TALENTPILOT_INPUT.job.department, stage: TALENTPILOT_INPUT.target_stage},
  candidates: results, checked_at: new Date().toISOString()
}));
