const task = await useOrCreateTaskSpace('TalentPilot 岗位JD只读匹配');
await openOrReuseTab('https://www.italent.cn/', {wait:true, timeout:20});
async function until(expression) {
  for (let n = 0; n < 25; n++) {
    if (await js(expression)) return;
    await wait(1);
  }
  throw new Error('JD page unavailable');
}
await until(`Boolean(document.querySelector('[data-udc*="86f5f64f-2d79-4eed-a855-3d77ddf17543"]'))`);
await js(`document.querySelector('[data-udc*="86f5f64f-2d79-4eed-a855-3d77ddf17543"]').click()`);
await until(`(()=>{try{return Boolean(document.querySelector('iframe')?.contentDocument.querySelector('[data-column-key="JobTitle"]'))}catch{return false}})()`);
const jobId = TALENTPILOT_INPUT.job_id;
const top = await js(`(() => { const r=document.querySelector("iframe").getBoundingClientRect(); return {x:r.x+500,y:r.y+300}; })()`);
await cdp("Input.dispatchMouseEvent", {type:"mouseWheel",...top,deltaX:0,deltaY:-20000});
await wait(.5);
let clicked = false;
for (let n = 0; n < 40; n++) {
  clicked = await js(`(()=>{
    const d=document.querySelector('iframe').contentDocument;
    const a=[...d.querySelectorAll('a')].find(e=>e.textContent.includes('（'+${JSON.stringify(jobId)}+'）')||e.textContent.includes('('+${JSON.stringify(jobId)}+')'));
    if(!a)return false;a.click();return true;
  })()`);
  if (clicked) break;
  const point = await js(`(()=>{const r=document.querySelector('iframe').getBoundingClientRect();return {x:r.x+500,y:r.y+300}})()`);
  await cdp('Input.dispatchMouseEvent', {type:'mouseWheel',...point,deltaX:0,deltaY:420});
  await wait(.25);
}
if (!clicked) throw new Error('Job not in current authorized grid');
await until(`(()=>{try{const d=document.querySelector('iframe').contentDocument;return d.body.innerText.includes(${JSON.stringify(jobId)})&&[...d.querySelectorAll('.form-item__title')].some(e=>e.textContent.trim()==='工作职责')}catch{return false}})()`);
const result = await js(`(()=>{
  const frame=document.querySelector('iframe'), d=frame.contentDocument;
  const field=label=>{
    const item=[...d.querySelectorAll('.form-item')].find(e=>e.querySelector('.form-item__title')?.textContent.trim()===label);
    return item?.querySelector('.form-item__control')?.textContent.trim()||'';
  };
  const url=new URL(frame.src);
  return {job_id:${JSON.stringify(jobId)},title:field('职位名称'),department:field('需求部门'),
    duties:field('工作职责'),qualifications:field('任职资格'),recruitment_type:field('招聘类别'),
    job_uuid:url.searchParams.get('id'),numeric_job_id:url.searchParams.get('jobId')};
})()`);
if (!result.title || result.duties.length < 20 || result.qualifications.length < 20)
  throw new Error('Incomplete JD');
cliLog('TALENTPILOT_RESULT:'+JSON.stringify({state:'verified_job_jd',...result}));
