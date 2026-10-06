(() => {
  let report = null, page = 0, run = null, loading = false, busy = false;
  const pageSize = 3;
  const active = new Set(["queued", "running", "waiting_for_audit"]);
  const stepNames = {positions:"岗位", applications:"候选人", interviews:"面试", validate_import:"核对数据", audit:"统计", report:"生成报表"};
  const cell = value => value === null || value === undefined || value === ""
    ? '<span class="ledger-empty" title="未采集到对应证据">—</span>' : esc(value);

  function selectedGroups() {
    const job = $("report-job").value;
    return (report?.groups || []).filter(group => !job || group.job_id === job);
  }
  function filteredGroups() {
    const term = $("ledger-search").value.trim().toLocaleLowerCase();
    return selectedGroups().map(group => {
      if (!term || `${group.title} ${group.job_id}`.toLocaleLowerCase().includes(term)) return group;
      return {...group, rows:group.rows.filter(row => row.name.toLocaleLowerCase().includes(term))};
    }).filter(group => !term || group.rows.length);
  }
  function tableMarkup(groups) {
    const widths = [190,110,100,100,60,70,250,200,140,110,140,110,140,110,140,110,140,180];
    const headings = report.columns.map((name, index) => `<th scope="col" class="${index===0?"ledger-job":index===1?"ledger-code":""}"${name==="Full"?' title="仅显示来源中的 Full 字段，不推算满编"':""}>${esc(name)}</th>`).join("");
    const body = groups.map(group => {
      const rows = group.rows.length ? group.rows : [{cells:Array(9).fill("")}];
      return `<tbody data-job="${esc(group.job_id)}">` + rows.map((row, index) => {
        const shared = index ? "" : group.shared.map((value, col) => `<td rowspan="${rows.length}" class="ledger-shared ${col===0?"ledger-job":col===1?"ledger-code":""}">${cell(value)}</td>`).join("");
        return `<tr class="${index?"":"job-start"}">${shared}${row.cells.map((value, col) => `<td class="${[2,4,6].includes(col)?"ledger-rejected":col===8?"ledger-note":""}">${cell(value)}</td>`).join("")}</tr>`;
      }).join("") + "</tbody>";
    }).join("");
    return `<table class="ledger-table"><colgroup>${widths.map(width=>`<col style="width:${width}px">`).join("")}</colgroup><thead><tr>${headings}</tr></thead>${body}</table>`;
  }
  function renderLedger() {
    const groups = filteredGroups(), count = groups.reduce((total, group) => total + group.rows.length, 0);
    const pages = Math.max(1, Math.ceil(groups.length/pageSize));
    page = Math.min(page, pages-1);
    $("ledger-count").textContent = `${groups.length} 个岗位 · ${count} 位候选人（按岗位去重）`;
    $("ledger-table").innerHTML = groups.length ? tableMarkup(groups.slice(page*pageSize, (page+1)*pageSize)) : '<div class="report-empty">暂无匹配记录</div>';
    $("ledger-pagination").classList.toggle("hidden", pages < 2);
    $("ledger-page").textContent = `第 ${page+1} / ${pages} 页`;
    $("ledger-prev").disabled = page === 0;
    $("ledger-next").disabled = page >= pages-1;
    $("ledger-download").disabled = !groups.length;
  }
  function renderFunnel() {
    const groups = selectedGroups(), stages = report.stages || [];
    const counts = stages.map(stage => groups.reduce((total, group) => total + group.stages[stage], 0));
    const maximum = Math.max(1, ...counts);
    $("funnel-chart").innerHTML = stages.length ? stages.map((stage, index) =>
      `<div class="funnel-stage"><b>${counts[index]}</b><span>${esc(stage)}</span><div class="funnel-track" aria-hidden="true"><div class="funnel-bar" style="height:${counts[index]/maximum*100}%"></div></div></div>`).join("")
      : '<div class="report-empty">暂无报表，点击更新报表</div>';
    $("round-summary").innerHTML = groups.length ? ["一面","二面","三面"].map(round => {
      const total = groups.reduce((n, group) => n+group.rounds[round].count, 0);
      const rejected = groups.reduce((n, group) => n+group.rounds[round].rejected, 0);
      return `<span>${round}已记录<b>${total}</b>人 <span class="round-rejected">淘汰 ${rejected}</span></span>`;
    }).join("") : "";
  }
  async function loadReport() {
    if (loading) return;
    loading = true;
    try {
      const data = await api("/recruitment-report");
      if (report && report.dataset_id === data.dataset_id) return;
      report = data;
      const chosen = $("report-job").value;
      $("report-job").innerHTML = '<option value="">全部岗位</option>' + data.groups.map(group=>`<option value="${esc(group.job_id)}">${esc(group.title)}（${esc(group.job_id)}）</option>`).join("");
      if (data.groups.some(group=>group.job_id===chosen)) $("report-job").value=chosen;
      page = 0;
      $("report-source").textContent = data.dataset_id
        ? `本地快照 ${new Date(data.captured_at).toLocaleString("zh-CN",{hour12:false})} · 当前阶段人数（不含淘汰）${data.interviews_collected?"":" · 面试历史未采集"}`
        : "尚无已核验的招聘数据";
      renderFunnel();
      renderLedger();
    } catch (error) {
      $("report-source").textContent = `读取失败：${error.message}`;
      throw error;
    } finally { loading = false; }
  }
  async function loadRun() {
    const runs = await api("/live-runs");
    run = runs[0] || null;
    const running = run && active.has(run.status);
    const paused = run && ["waiting_for_user", "failed"].includes(run.status);
    $("report-refresh").disabled = busy || Boolean(running);
    $("report-resume").classList.toggle("hidden", !paused);
    $("report-cancel").classList.toggle("hidden", !(running || paused));
    $("report-progress").textContent = running ? `正在读取${stepNames[run.step] || "数据"}…`
      : paused ? `采集暂停：${run.error || "请检查北森登录状态"}`
      : run?.status === "cancelled" ? "采集已取消，保留原报表"
      : run ? "本次采集已完成，报表已更新" : "";
  }
  $("report-job").onchange = () => { page=0; renderFunnel(); renderLedger(); };
  $("ledger-search").oninput = () => { page=0; renderLedger(); };
  $("ledger-prev").onclick = () => { page--; renderLedger(); $("ledger-table").scrollTop=0; };
  $("ledger-next").onclick = () => { page++; renderLedger(); $("ledger-table").scrollTop=0; };
  $("report-refresh").onclick = () => action(async () => {
    busy = true; $("report-refresh").disabled=true;
    try {
      await api("/live-runs", {request_key:"report-"+crypto.randomUUID()});
      await loadRun();
    } finally { busy=false; $("report-refresh").disabled=Boolean(run && active.has(run.status)); }
  });
  $("report-resume").onclick = () => action(async () => {
    if (!run || !confirm("确认已完成北森登录，并已交还浏览器控制权？")) return;
    await api(`/live-runs/${run.id}/resume`, {});
    await loadRun();
  });
  $("report-cancel").onclick = () => action(async () => {
    if (!run) return;
    await api(`/live-runs/${run.id}/cancel`, {});
    await loadRun();
  });
  $("ledger-download").onclick = () => {
    // HTML preserves merged cells without pretending a CSV is an Excel workbook.
    const content = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>招聘台账</title><style>body{font-family:sans-serif}table{border-collapse:collapse;font-size:13px}td,th{border:1px solid #ccd5d0;padding:10px;vertical-align:top;white-space:pre-line}th{background:#edf2ef}td{min-width:80px}.ledger-empty{color:#84918c}</style><h1>招聘台账</h1><p>${esc($("report-source").textContent)}</p>${tableMarkup(filteredGroups())}</html>`;
    const url = URL.createObjectURL(new Blob([content], {type:"text/html;charset=utf-8"}));
    const link = document.createElement("a");
    link.href=url; link.download="招聘台账.html"; link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  action(async () => { await loadReport(); await loadRun(); });
  let polling = false;
  setInterval(async () => {
    if (polling || document.hidden) return;
    polling=true;
    try { await loadReport(); await loadRun(); } catch (error) { $("report-progress").textContent=error.message; }
    finally { polling=false; }
  }, 8000);
})();
