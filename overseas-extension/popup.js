const $ = (id) => document.getElementById(id);
const send = (message) => new Promise((resolve, reject) => {
  chrome.runtime.sendMessage(message, (result) => {
    if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
    else if (!result?.ok) reject(new Error(result?.error || "保存失败"));
    else resolve(result.data);
  });
});

async function loadJobs() {
  try {
    const jobs = await send({type: "list_jobs"});
    $("job").replaceChildren(...jobs.map((job) => {
      const option = new Option(`${job.title}（${job.job_id}）`, job.job_id);
      option.dataset.title = job.title;
      return option;
    }));
    $("job").disabled = !jobs.length;
    $("collect").disabled = !jobs.length;
    $("status").textContent = jobs.length ? "已连接 TalentPilot。" : "当前没有可选岗位。";
  } catch (error) {
    $("status").textContent = error.message;
    $("status").className = "error";
  }
}

$("collect").addEventListener("click", async () => {
  const option = $("job").selectedOptions[0];
  if (!option || !$("confirmed").checked || !$("evidence").value.trim()) {
    $("status").textContent = "请选择岗位并填写、确认授权依据。";
    $("status").className = "error";
    return;
  }
  $("collect").disabled = true;
  $("status").className = "";
  $("status").textContent = "正在完整读取当前候选人档案…";
  try {
    const result = await send({
      type: "collect_profile",
      job_id: option.value,
      job_title: option.dataset.title,
      region: $("region").value.trim(),
      consent_evidence: $("evidence").value.trim(),
      retention_days: Number($("retention").value),
    });
    $("status").textContent = result.duplicate ? "该档案已经保存。" : "候选人档案已保存。";
  } catch (error) {
    $("status").textContent = error.message;
    $("status").className = "error";
  } finally {
    $("collect").disabled = false;
  }
});

loadJobs();
