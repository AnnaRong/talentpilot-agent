(() => {
  const names={locked:"等待解锁",expiring:"即将到期",verify_due:"预计已到期，待核验",unlocked:"已解锁，待核验",
    unknown:"时间 / 状态待核实",stale:"数据已过期",unavailable:"无权限或已删除",out_of_scope:"已离开监测范围",lost:"原岗位已掉锁"};
  const sources={upload:"上传关联",recruiting:"我的招聘",followed:"关注回捞"};
  let data=null,tab="upload",busy=false,offset=0,page=0;
  const message=(text,error=false)=>{$("lock-message").textContent=text;$("lock-message").className=error?"error":"muted"};
  const when=value=>value?new Date(value).toLocaleString("zh-CN",{hour12:false}):"未返回";
  const safeUrl=value=>{
    try{const url=new URL(value);return url.protocol==="https:"&&["www.italent.cn","cloud.italent.cn"].includes(url.hostname)?url.href:null}catch{return null}
  };
  function countdown(row) {
    if(!row.fresh)return "等待刷新";
    if(row.state==="lost")return "原岗位锁已失效";
    if(row.state==="unlocked")return "实时状态已解锁";
    if(!row.unlock_at)return "未返回预计时间";
    const seconds=Math.ceil((Date.parse(row.unlock_at)-Date.now()-offset)/1000);
    if(seconds<=0)return "预计已到期，需复查";
    const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60);
    return hours>=24?`${Math.floor(hours/24)}天 ${hours%24}小时`:`${hours}小时 ${minutes}分钟`;
  }
  function renderTable() {
    if(!data)return;
    const filter=$("lock-filter").value,query=$("lock-search").value.trim().toLowerCase();
    const rows=data.items.filter(r=>r.source===tab).filter(r=>{
      const haystack=[r.name,r.job_id,r.job_title].join(" ").toLowerCase();
      return (!query||haystack.includes(query))&&(filter==="all"||
        (filter==="attention"&&["expiring","verify_due","unlocked","lost"].includes(r.state))||
        (filter==="locked"&&["locked","expiring"].includes(r.state))||
        (filter==="unknown"&&["unknown","stale","unavailable"].includes(r.state)));
    });
    $("lock-content").setAttribute("aria-labelledby","lock-tab-"+tab);
    page=Math.min(page,Math.max(0,Math.ceil(rows.length/20)-1));
    $("lock-pagination").classList.toggle("hidden",rows.length<=20);
    $("lock-page-label").textContent=`共 ${rows.length} 条 · ${page+1} / ${Math.max(1,Math.ceil(rows.length/20))} 页`;
    $("lock-prev").disabled=page===0;$("lock-next").disabled=(page+1)*20>=rows.length;
    $("lock-table").innerHTML=rows.length?`<table><thead><tr><th>候选人 / 来源</th><th>关联岗位</th><th>锁状态</th><th>距预计解锁</th><th>最近核验</th><th>处理</th></tr></thead><tbody>${rows.slice(page*20,(page+1)*20).map(r=>{
      const url=safeUrl(r.detail_url);
      const statusLabel=r.state==="unknown"&&r.locked===true?"已锁定，时间待核实":names[r.state]||r.state;
      return `<tr><td><b>${esc(r.name||"姓名未返回")}</b><div class="lock-cell-meta">${r.provenance==="upload_blocked"?"核验被锁，未上传":esc(sources[r.source])}</div></td><td>${esc(r.job_title||"岗位未返回")}<div class="lock-cell-meta">${esc(r.job_id)}</div></td><td><span class="lock-status ${esc(r.state)}">${esc(statusLabel)}</span><div class="lock-cell-meta">${esc(r.lock_label||"锁类型未返回")}</div></td><td><div class="lock-countdown">${esc(countdown(r))}</div><div class="lock-cell-meta">${r.unlock_at?esc(when(r.unlock_at)):"到期时间待北森返回"}</div></td><td>${esc(when(r.observed_at))}</td><td>${r.source==="followed"&&r.state==="unlocked"?`<button data-lock-recall="${esc(r.id)}" class="secondary" type="button">回捞核验</button>`:url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">查看北森</a>`:'<span class="muted">待核验</span>'}</td></tr>`;
    }).join("")}</tbody></table>`:`<div class="lock-empty"><b>${query||filter!=="all"?"没有符合条件的候选人":({upload:"暂无上传关联的锁记录",recruiting:data.last_success?"当前采集范围内暂无职位锁记录":"尚未读取招聘锁状态",followed:data.last_success?"当前关注清单为空":"尚未读取关注清单"})[tab]}</b><p>${({upload:"当前上传仅有核验预览，未产生上传成功回执。",recruiting:"范围以本次北森招聘列表的流程和职位筛选为准。",followed:"回捞承接岗位尚未配置，自动写入未启用。"})[tab]}</p></div>`;
    const scopes=[...new Set(data.items.filter(r=>r.source===tab&&r.scope).map(r=>r.scope))];
    $("lock-scope").textContent=tab==="upload"?"来源：上传核验中的被锁记录；未关联关注清单的候选人，锁状态需重新核验。":
      tab==="recruiting"?`范围：${scopes.join("；")||"尚未获取"}。保留职位锁及后续掉锁变化；未知时间不计入临期数，不代表全公司或其他流程。`:
      `来源：我关注的应聘者。${data.skill_installed?"回捞 Skill 已接入":"回捞 Skill 缺失"}；推荐写入与回读尚未验真。`;
  }
  function render() {
    const counts=data.counts;
    const waitingStates=["locked","expiring","verify_due"];
    const waiting=data.items.filter(r=>r.fresh&&r.active!==false&&
      (waitingStates.includes(r.state)||(r.state==="unknown"&&r.locked===true))).length;
    const needsReview=data.items.filter(r=>r.active!==false&&
      (r.state==="stale"||r.state==="unavailable"||(r.state==="unknown"&&r.locked!==true))).length;
    $("lock-metrics").innerHTML=[["等待解锁",waiting,"info"],["即将到期",counts.expiring+counts.verify_due,"danger"],
      ["掉锁 / 解锁待处理",counts.unlocked+(counts.lost||0),"ready"],["待刷新 / 核实",needsReview,""]]
      .map(([label,count,color])=>`<div class="lock-metric ${color}"><span>${label}</span><strong>${count}</strong></div>`).join("");
    const unread=data.notifications.filter(n=>!n.read_at).length;
    $("lock-unread").textContent=unread?`${unread} 条未读提醒`:"";
    $("lock-inbox-count").textContent=unread?`（${unread} 未读）`:"";
    $("lock-health").textContent=`${data.running?"正在采集":data.settings.enabled?"定时监测已开启":"定时监测已暂停"} · 最近成功：${data.last_success?when(data.last_success):"尚未完成"}${data.next_due?` · 下次：${when(data.next_due*1000)}`:""}`;
    if(data.last_error&&!busy)message(data.last_error,true);
    $("lock-notifications").innerHTML=data.notifications.map(n=>`<div class="lock-notification"><span class="lock-status ${esc(n.state)}">${n.read_at?"已读":"未读"} · ${esc(names[n.state])}</span><span>${esc(n.name||"候选人")} · ${esc(sources[n.source])} · ${esc(n.job_id||"岗位待核实")}</span><time>${esc(when(n.created_at))}</time></div>`).join("")||'<p class="muted">暂无提醒。缺少锁时间不代表没有掉锁风险。</p>';
    $("lock-acknowledge").disabled=!unread;
    renderTable();
  }
  async function load(settings=false) {
    data=await api("/lock-monitor");offset=Date.parse(data.server_now)-Date.now();
    if(settings){
      $("lock-enabled").checked=data.settings.enabled;$("lock-interval").value=data.settings.interval_minutes;$("lock-warning").value=data.settings.warning_hours;
      if(!data.items.some(r=>r.source==="upload")&&data.items.some(r=>r.source==="recruiting")){
        tab="recruiting";document.querySelectorAll("[data-lock-tab]").forEach(b=>b.setAttribute("aria-selected",String(b.dataset.lockTab===tab)));
      }
    }
    render();
  }
  $("lock-settings-open").onclick=()=>$("lock-settings").classList.toggle("hidden");
  $("lock-settings").onsubmit=event=>{
    event.preventDefault();
    action(async()=>{
      const button=event.submitter;button.disabled=true;
      try{await api("/lock-monitor/settings",{enabled:$("lock-enabled").checked,interval_minutes:Number($("lock-interval").value),warning_hours:Number($("lock-warning").value)});
        message("提醒设置已保存；未授权任何外部写入。");await load();$("lock-settings").classList.add("hidden")
      }finally{button.disabled=false}
    });
  };
  $("lock-refresh").onclick=async()=>{
    if(busy)return;busy=true;$("lock-refresh").disabled=true;message("正在读取北森关注清单与招聘锁状态…");
    try{await api("/lock-monitor/refresh",{});message("只读刷新已完成，未修改候选人状态。")}
    catch(error){message(error.message,true)}
    finally{busy=false;$("lock-refresh").disabled=false;await load().catch(error=>message(error.message,true))}
  };
  document.querySelectorAll("[data-lock-tab]").forEach(button=>{
    button.onclick=()=>{tab=button.dataset.lockTab;page=0;document.querySelectorAll("[data-lock-tab]").forEach(b=>b.setAttribute("aria-selected",String(b===button)));renderTable()};
    button.onkeydown=event=>{
      if(!["ArrowLeft","ArrowRight"].includes(event.key))return;
      const tabs=[...document.querySelectorAll("[data-lock-tab]")],index=tabs.indexOf(button);
      const next=tabs[(index+(event.key==="ArrowRight"?1:tabs.length-1))%tabs.length];next.click();next.focus();event.preventDefault();
    };
  });
  $("lock-filter").onchange=()=>{page=0;renderTable()};$("lock-search").oninput=()=>{page=0;renderTable()};
  $("lock-prev").onclick=()=>{page--;renderTable()};$("lock-next").onclick=()=>{page++;renderTable()};
  $("lock-acknowledge").onclick=()=>action(async()=>{await api("/lock-monitor/acknowledge",{});await load()});
  $("lock-table").onclick=event=>{
    const button=event.target.closest("[data-lock-recall]");if(!button)return;
    const row=data.items.find(r=>r.id===button.dataset.lockRecall);if(!row)return;
    const dialog=document.createElement("dialog");dialog.className="lock-dialog";dialog.setAttribute("aria-labelledby","lock-recall-title");
    dialog.innerHTML=`<form id="lock-recall-form"><h3 id="lock-recall-title">回捞核验</h3><p><b>${esc(row.name||"候选人")}</b> · ${esc(row.job_title||"来源岗位未返回")}</p><p class="muted">当前仅生成本地核验单；尚未执行推荐或续锁。</p><label for="lock-target-job">承接岗位</label><select id="lock-target-job" required><option value="">请选择已刷新岗位</option>${state.positions.map(p=>`<option value="${esc(p.job_id)}">${esc(p.title)}（${esc(p.job_id)}）</option>`).join("")}</select><label for="lock-target-stage">目标阶段 / 状态</label><input id="lock-target-stage" required maxlength="100" placeholder="请输入准确的目标阶段 / 状态"><div id="lock-recall-result" role="status"></div><div class="actions"><button type="submit">生成核验单</button><button id="lock-recall-close" type="button" class="secondary">关闭</button></div></form>`;
    document.body.append(dialog);dialog.showModal();
    const close=()=>{dialog.close();dialog.remove();button.focus()};$("lock-recall-close").onclick=close;dialog.addEventListener("cancel",e=>{e.preventDefault();close()});
    $("lock-recall-form").onsubmit=async event=>{
      event.preventDefault();const submit=event.submitter;submit.disabled=true;
      try{
        const preview=await api("/lock-monitor/recall-preview",{watch_id:row.id,job_id:$("lock-target-job").value,target_stage:$("lock-target-stage").value});
        $("lock-recall-result").innerHTML=`<p><b>${esc(preview.job_title)}（${esc(preview.job_id)}）</b> · ${esc(preview.target_stage)}</p><ol>${preview.checks.map(c=>`<li>${esc(c)}：待实时核验</li>`).join("")}</ol><p class="error">写入接口与逐人回读尚未验真，未执行回捞。</p>`;
      }catch(error){$("lock-recall-result").textContent=error.message}
      finally{submit.disabled=false}
    };
  };
  load(true).catch(error=>message(error.message,true));
  setInterval(()=>{if(!document.hidden&&!busy)load().catch(error=>message(error.message,true))},15000);
})();
