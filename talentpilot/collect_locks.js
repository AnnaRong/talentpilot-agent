// Read only page-generated list requests. Never replay signed metadata.
await useOrCreateTaskSpace('TalentPilot 锁状态只读连接');
await openOrReuseTab('https://www.italent.cn/', {wait:true, timeout:20});
async function until(expression, message) {
  for (let n=0;n<120;n++) {
    const value=await js(expression);
    if(value)return value;
    await wait(.25);
  }
  throw new Error(message);
}
await until(`Boolean(document.querySelector('[data-udc*="3308e886-69b6-4eec-b4e0-ac3865842357"]'))`, 'Manual login required');
await js(`window.__tpLockPages={};window.__tpLockError=null`);
function installLockObserver() {
  if(window.__tpLockObserver)return;
  window.__tpLockObserver=true;
  const original=window.fetch.bind(window);
  window.fetch=async function(input,options) {
    const response=await original(input,options);
    const url=new URL(typeof input==='string'?input:input.url,location.href);
    const view=url.searchParams.get('viewName');
    const source=view==='Recruitment.SingleObjectListView.ApplicantAttentionList'?'followed':
      view==='Recruitment.SingleObjectListView.ApplyCardList'?'recruiting':null;
    if(source&&url.pathname==='/api/v2/UI/TableList') {
      try {
        const body=await response.clone().json(),paging=body.cmp_data?.paging;
        if(!paging||!Array.isArray(body.biz_data))throw new Error('Invalid paging');
        const records=body.biz_data.map(row=>{
          const cell=(name)=>{
            if(row[name])return row[name];
            const matches=Object.keys(row).filter(k=>k.endsWith('~'+name)||k.endsWith('.'+name));
            const values=matches.map(k=>row[k]).filter(v=>v?.value!==null&&v?.value!==undefined&&v?.value!=='');
            if(new Set(values.map(v=>JSON.stringify(v.value))).size>1)return null;
            return values[0]||null;
          };
          const value=name=>cell(name)?.value??null,text=name=>cell(name)?.text??null;
          const boolean=v=>v===true||v==='True'||v===1||v==='1'?true:
            v===false||v==='False'||v===0||v==='0'?false:null;
          const permission=boolean(value('HasPermission')),deleted=boolean(value('StdIsDeleted'));
          const manual=value('ApplicantLockStatus'),apply=value(source==='followed'?'ApplicationLockStatus':'ApplyLockStatus');
          const labels=[text('ApplicantLockStatus'),text('ApplicationLockStatus'),text('ApplyLockStatus')].filter(Boolean);
          // Only the explicitly observed zero status means unlocked; missing is unknown.
          const knownManual=['0','1'].includes(String(manual)),knownApply=['0','1','2'].includes(String(apply));
          let locked=knownManual&&knownApply?(String(manual)!=='0'||String(apply)!=='0'):null;
          if(labels.some(t=>/^(当前职位锁定|其他职位锁定|已锁定)$/.test(t)))locked=true;
          const jobLabel=text('JobId')||'',jobCode=jobLabel.match(/[（(](J\d+)[）)]/);
          const rawTime=value('PredictUnlockTime');
          let unlockAt=null;
          if(typeof rawTime==='string'&&/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(rawTime)&&Number.isFinite(Date.parse(rawTime)))unlockAt=new Date(rawTime).toISOString();
          const candidateId=value(source==='followed'?'ApplicantID':'ApplicantId');
          if(!candidateId||(source==='recruiting'&&(!value('_id')||!jobCode)))throw new Error('Missing stable identity');
          return {source,candidate_id:String(candidateId),name:source==='followed'?text('Name'):text('ApplicantId'),
            application_id:source==='recruiting'?String(value('_id')):null,
            job_id:jobCode?.[1]||'',job_title:jobLabel.replace(/[（(]J\d+[）)]/,'').trim(),
            locked,lock_label:labels.join(' / '),unlock_at:unlockAt,has_permission:permission,deleted,
            followed:source==='followed'?boolean(value('IsAttention')):null,
            phase:text('ProcessPhaseId'),status:text('ProcessStatusId'),
            current_job_locked:source!=='recruiting'?null:
              text('ApplyLockStatus')==='当前职位锁定'?true:
              ['未被职位锁定','其他职位锁定'].includes(text('ApplyLockStatus'))?false:null};
        });
        const root=window.parent;
        if(!root.__tpLockPages)root.__tpLockPages={};
        if(!root.__tpLockPages[source])root.__tpLockPages[source]={};
        root.__tpLockPages[source][paging.page]={...paging,records,server_date:response.headers.get('date')};
      } catch {
        window.parent.__tpLockError='锁字段、分页或稳定身份发生变化，停止本次采集';
      }
    }
    return response;
  };
}
const observer='('+installLockObserver.toString()+')()';
const injected=await cdp('Page.addScriptToEvaluateOnNewDocument',{source:observer});
const records=[],coverage={};
try {
  for(const source of ['followed','recruiting']) {
    const menu=source==='followed'?'3308e886-69b6-4eec-b4e0-ac3865842357':'2d0174f6-e4d5-4e84-8e3f-5e28fcfeb265';
    await js(`document.querySelector('[data-udc*="${menu}"]').click()`);
    if(source==='followed') {
      await until(`(()=>{try{return document.querySelector('iframe').contentDocument.body.innerText.includes('我关注的应聘者')}catch{return false}})()`, '关注入口未加载');
      await js(`(()=>{const d=document.querySelector('iframe').contentDocument;const e=[...d.querySelectorAll('*')].find(e=>e.children.length===0&&e.textContent.trim()==='我关注的应聘者');if(!e)throw new Error('关注入口缺失');e.click()})()`);
    } else {
      await until(`(()=>{try{return document.querySelector('iframe').contentDocument.body.innerText.includes('全部简历')}catch{return false}})()`, '招聘列表未加载');
      // Discard the initial stage-filtered list before selecting all applications.
      await js(`window.__tpLockPages.recruiting={};document.querySelector('iframe').contentWindow.eval(${JSON.stringify(observer)})`);
      await js(`(()=>{const d=document.querySelector('iframe').contentDocument;[...d.querySelectorAll('*')].find(e=>e.textContent.trim()==='全部简历').click()})()`);
    }
    const expectedTotal=source==='recruiting'?await until(`(()=>{const e=document.querySelector('iframe').contentDocument.querySelector('.recruit-paas-all-resume--active .recruit-paas-all-resume__count');return e?{total:Number(e.textContent.replace(/,/g,''))}:null})()`, '全部简历范围未生效'):null;
    let total=null,capacity=null;
    const seen=new Set();
    const scope=source==='followed'?'我关注的应聘者':await js(`document.querySelector('iframe').contentDocument.body.innerText.split('\\n').slice(0,2).join(' / ')`);
    for(let page=0;;page++) {
      const result=await until(`(()=>{if(window.__tpLockError)throw new Error(window.__tpLockError);const p=window.__tpLockPages?.${source}?.[${page}];return p&&${expectedTotal?`p.total===${expectedTotal.total}`:'true'}?p:null})()`, '分页未完整返回，未更新锁清单');
      if(!Number.isInteger(result.total)||result.total<0||!Number.isInteger(result.capacity)||result.capacity<1)throw new Error('Invalid coverage');
      if(total!==null&&(total!==result.total||capacity!==result.capacity))throw new Error('分页总量变化，请重新读取');
      total=result.total;capacity=result.capacity;
      if(result.records.length!==Math.min(capacity,total-page*capacity))throw new Error('分页缺失');
      for(const row of result.records) {
        const id=source==='followed'?row.candidate_id:row.application_id;
        if(seen.has(id))throw new Error('重复分页，未更新清单');
        seen.add(id);
        if(row.has_permission!==true||row.deleted===true)continue;
        if(source==='followed'&&row.followed!==true)continue;
        // Previously monitored applications must remain observable after losing their lock.
        records.push({...row,scope});
      }
      if(seen.size===total)break;
      await until(`(()=>{const d=document.querySelector('iframe').contentDocument;return [...d.querySelectorAll('a')].some(e=>e.textContent.trim()==='下一页'&&!e.className.includes('disabled'))})()`, '下一页控件未就绪');
      await wait(.3);
      await js(`(()=>{const d=document.querySelector('iframe').contentDocument;const e=[...d.querySelectorAll('a')].find(e=>e.textContent.trim()==='下一页');if(!e)throw new Error('下一页缺失');e.click()})()`);
    }
    coverage[source]={declared:total,captured:seen.size,scope};
  }
} finally {
  await cdp('Page.removeScriptToEvaluateOnNewDocument',{identifier:injected.identifier});
}
cliLog('TALENTPILOT_RESULT:'+JSON.stringify({state:'verified_lock_inventory',complete:true,records,coverage}));
