const task = await useOrCreateTaskSpace('TalentPilot 岗位只读连接');
await openOrReuseTab('https://www.italent.cn/', {wait:true, timeout:20});
let ready = false;
for (let n = 0; n < 15; n++) {
  ready = await js(`Boolean(document.querySelector('[data-udc*="86f5f64f-2d79-4eed-a855-3d77ddf17543"]'))`);
  if (ready) break;
  await wait(1);
}
if (!ready) {
  cliLog('TALENTPILOT_RESULT:' + JSON.stringify({state:'awaiting_login',reason:'请在Ego北森页面完成登录后重试'}));
} else {
  await js(`document.querySelector('[data-udc*="86f5f64f-2d79-4eed-a855-3d77ddf17543"]').click()`);
  for (let n = 0; n < 20; n++) {
    ready = await js(`(()=>{try{return Boolean(document.querySelector('iframe')?.contentDocument.querySelector('[data-column-key="JobTitle"]'))}catch{return false}})()`);
    if (ready) break;
    await wait(1);
  }
  const positions = new Map();
  let declared = null;
  if (ready) {
    const point=await js(`(()=>{const r=document.querySelector('iframe').getBoundingClientRect();return {x:r.x+500,y:r.y+300}})()`);
    await cdp('Input.dispatchMouseEvent',{type:'mouseWheel',...point,deltaX:0,deltaY:-20000});
    await wait(1);
  }
  for (let n = 0; ready && n < 35; n++) {
    const page = await js(String.raw`(()=>{
      const frame=document.querySelector('iframe'),d=frame.contentDocument;
      const total=d.body.innerText.match(/共\s*(\d+)\s*条/);
      const headers=[...d.querySelectorAll('[role="columnheader"]')].map(e=>e.getAttribute('data-column-key'));
      const rows=[...d.querySelectorAll('[role="row"]')].map(e=>[...e.querySelectorAll('[role="gridcell"]')].map(c=>c.innerText.trim())).filter(r=>r.length);
      const rect=frame.getBoundingClientRect();
      return {total:total?Number(total[1]):null,headers,rows,x:rect.x+500,y:rect.y+300};
    })()`);
    if (declared !== null && page.total !== declared) throw new Error('Inventory changed during collection');
    declared = page.total;
    for (const cells of page.rows) {
      const title=cells[page.headers.indexOf('JobTitle')];
      const code=title?.match(/[（(](J\d+)[）)]/);
      if (!code) continue;
      const field=k=>cells[page.headers.indexOf(k)] || null;
      positions.set(code[1],{job_id:code[1],title:title.replace(/[（(]J\d+[）)]/,'').trim(),
        recruiter:field('HrDutyUser'),department:field('OrgId'),hc:field('HeadCount'),
        source_kind:'visible_job_grid',uuid_verified:false});
    }
    if (declared && positions.size===declared) break;
    await cdp('Input.dispatchMouseEvent',{type:'mouseWheel',x:page.x,y:page.y,deltaX:0,deltaY:420});
    await wait(.2);
  }
  cliLog('TALENTPILOT_RESULT:'+JSON.stringify({
    state:declared && positions.size===declared?'verified_job_inventory':'incomplete',
    declared,captured:positions.size,positions:[...positions.values()],
    reason:'仅接受完整岗位清单；简历、面试和Offer不随岗位读取自动刷新'
  }));
}
