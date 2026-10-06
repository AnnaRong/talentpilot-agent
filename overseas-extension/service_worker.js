const API = "http://127.0.0.1:8765/api/platform/connectors/overseas";
const VERSION = 1;

async function request(path, options = {}) {
  const response = await fetch(API + path, {
    ...options,
    headers: {"Content-Type": "application/json", "X-TalentPilot-Extension": "overseas-v1", ...(options.headers || {})},
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "TalentPilot 连接失败");
  return data;
}

function canonicalProfileUrl(value) {
  const url = new URL(value);
  const parts = url.pathname.split("/").filter(Boolean);
  if (url.protocol !== "https:" || url.hostname !== "www.linkedin.com" || parts[0] !== "in" || !parts[1]) {
    throw new Error("请先打开 LinkedIn 候选人个人主页");
  }
  return `https://www.linkedin.com/in/${parts[1]}`;
}

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function activeLinkedInTab() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab?.id || !tab.url) throw new Error("没有可读取的当前标签页");
  canonicalProfileUrl(tab.url);
  return tab;
}

async function message(tabId, payload) {
  try {
    return await chrome.tabs.sendMessage(tabId, payload);
  } catch (_error) {
    await chrome.scripting.executeScript({target: {tabId}, files: ["content_script.js"]});
    await wait(150);
    return chrome.tabs.sendMessage(tabId, payload);
  }
}

async function navigate(tabId, url) {
  await chrome.tabs.update(tabId, {url});
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { chrome.tabs.onUpdated.removeListener(listener); reject(new Error("LinkedIn 页面加载超时")); }, 30000);
    const listener = (updatedId, changeInfo) => {
      if (updatedId === tabId && changeInfo.status === "complete") {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
  await wait(500);
}

async function collectProfile(input) {
  const tab = await activeLinkedInTab();
  const originalUrl = canonicalProfileUrl(tab.url);
  const prepared = await message(tab.id, {type: "AGENT_PROFILE_PREPARE", version: VERSION});
  if (prepared?.status !== "completed") throw new Error(prepared?.error || "候选人档案读取失败");
  const profile = {...prepared.data.profile};
  try {
    for (const kind of ["skills", "languages"]) {
      const detailUrl = prepared.data.detail_urls?.[kind];
      const expected = Number(prepared.data.detail_counts?.[kind]);
      if (!detailUrl || !Number.isInteger(expected) || expected < 1) continue;
      await navigate(tab.id, detailUrl);
      const detail = await message(tab.id, {
        type: "AGENT_PROFILE_DETAIL_EXTRACT", version: VERSION, detail_kind: kind,
        profile_url: originalUrl, expected_count: expected,
      });
      if (detail?.status !== "completed" || !Array.isArray(detail.data?.items) || detail.data.items.length !== expected) {
        throw new Error(detail?.error || `${kind} 详情读取不完整`);
      }
      profile[kind] = detail.data.items;
    }
  } finally {
    const current = await chrome.tabs.get(tab.id).catch(() => null);
    if (current?.url && canonicalProfileUrl(current.url.split("/details/")[0]) !== originalUrl) {
      throw new Error("候选人页面已变化，未保存本次档案");
    }
    if (current?.url !== originalUrl) await navigate(tab.id, originalUrl).catch(() => {});
  }
  profile.linkedin_url = originalUrl;
  return request("/profiles", {method: "POST", body: JSON.stringify({
    job_id: input.job_id,
    job_title: input.job_title,
    region: input.region,
    consent_evidence: input.consent_evidence,
    retention_days: input.retention_days,
    extracted_at: prepared.data.extracted_at || new Date().toISOString(),
    profile,
  })});
}

chrome.runtime.onMessage.addListener((messageValue, _sender, sendResponse) => {
  const task = messageValue?.type === "list_jobs"
    ? request("/jobs")
    : messageValue?.type === "collect_profile"
      ? collectProfile(messageValue)
      : Promise.reject(new Error("未知操作"));
  task.then((data) => sendResponse({ok: true, data})).catch((error) => sendResponse({ok: false, error: error.message}));
  return true;
});
