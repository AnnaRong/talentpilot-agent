const fs = require('fs');
const path = require('path');
const input = TALENTPILOT_INPUT;
const marker = value => cliLog('TALENTPILOT_EMAIL_RESULT:' + JSON.stringify(value));
const pause = seconds => new Promise(resolve => setTimeout(resolve, seconds * 1000));

function terms(title) {
  const whole = String(title || '').replace(/[（(].*?[）)]/g, '').trim();
  return [...new Set([whole, ...whole.split(/[\s/+·,，、_-]+/)])].filter(value => value.length >= 2);
}

async function start() {
  const task = await useOrCreateTaskSpace('TalentPilot 邮箱 ' + input.run_id);
  await openOrReuseTab(input.provider_url, {wait: true, timeout: 30});
  const page = await pageInfo();
  const handed = await handOffTaskSpace(task.id);
  marker({state:'awaiting_login', task_id:task.id, url:page.url, handoff_completed:handed.done === true});
}

async function stop() {
  await completeTaskSpace(input.task_id, {keep:false});
  marker({state:'cancelled', task_id:input.task_id});
}

async function searchInput() {
  return js(String.raw`(() => {
    const visible = element => {
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return box.width > 20 && box.height > 10 && style.visibility !== 'hidden' && style.display !== 'none';
    };
    const ranked = [...document.querySelectorAll('input:not([type=password]),textarea')].filter(visible)
      .map(element => {
        const text = [element.placeholder, element.getAttribute('aria-label'), element.name, element.id]
          .filter(Boolean).join(' ');
        return {element, score:/搜索|search|查找|find/i.test(text) ? 10 : element.type === 'search' ? 6 : 0};
      }).sort((a,b) => b.score-a.score);
    if (!ranked[0] || ranked[0].score < 1) return null;
    ranked[0].element.setAttribute('data-tp-email-search','1');
    return '[data-tp-email-search="1"]';
  })()`);
}

async function rows() {
  const expression = String.raw`(() => {
    const terms = __TP_TERMS__;
    const visible = element => {
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return box.width > 120 && box.height > 16 && style.visibility !== 'hidden' && style.display !== 'none';
    };
    const root = document.querySelector('[role=main]') || document.body;
    const selectors = ['tr','[role=main] [role=link]','[role=main] [role=option]','[role=main] [role=row]','[role=main] li'];
    const nodes = [...new Set(selectors.flatMap(selector => [...root.querySelectorAll(selector)]))];
    const selected = [];
    for (const element of nodes) {
      if (!visible(element)) continue;
      const text = (element.innerText || element.textContent || '').replace(/\\s+/g,' ').trim();
      if (text.length < 4 || text.length > 1200) continue;
      const resume = /(简历|应聘|求职|resume|curriculum vitae|\\bcv\\b)/i.test(text);
      const job = terms.some(term => text.toLowerCase().includes(term.toLowerCase()));
      const attachment = /\\.(pdf|docx?)\\b/i.test(text) || /(附件|attachment)/i.test(text);
      if (!(resume && (job || attachment))) continue;
      element.setAttribute('data-tp-email-row', String(selected.length));
      selected.push({selector:'[data-tp-email-row="'+selected.length+'"]', text:text.slice(0,500)});
      if (selected.length >= 20) break;
    }
    return selected;
  })()`.replace('__TP_TERMS__', JSON.stringify(terms(input.job_title)));
  return js(expression);
}

async function evidence() {
  const expression = String.raw`(() => {
    const terms = __TP_TERMS__;
    const root = document.querySelector('[role=main]') || document.body;
    const text = (root.innerText || '').replace(/\\s+/g,' ').trim();
    const intent = /(应聘|申请|求职|投递|resume for|apply for|application for)/i.test(text);
    const job = terms.some(term => text.toLowerCase().includes(term.toLowerCase()));
    const visible = element => {
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return box.width > 8 && box.height > 8 && style.visibility !== 'hidden' && style.display !== 'none';
    };
    const attachments = [];
    for (const element of [...root.querySelectorAll('a,button,[role=button],[download]')].filter(visible)) {
      const label = [element.innerText, element.textContent, element.getAttribute('aria-label'),
        element.getAttribute('title'), element.getAttribute('download'), element.href].filter(Boolean).join(' ');
      const match = label.match(/([^\\\\/:*?"<>|]{1,160}\\.(?:pdf|docx?))\\b/i);
      if (!match) continue;
      element.setAttribute('data-tp-email-attachment', String(attachments.length));
      attachments.push({selector:'[data-tp-email-attachment="'+attachments.length+'"]', filename:match[1].trim()});
      if (attachments.length >= 10) break;
    }
    return {intent, job, attachments};
  })()`.replace('__TP_TERMS__', JSON.stringify(terms(input.job_title)));
  return js(expression);
}

async function resume() {
  await takeOverTaskSpace(input.task_id);
  if (!await ensureRealTab()) throw new Error('邮箱任务空间没有可用页面');
  const page = await pageInfo();
  const text = await snapshotText();
  if (/login|signin|passport|auth/i.test(page.url) ||
      /(登录|登入|sign in|log in).{0,80}(密码|password|验证码|verification)/i.test(text)) {
    const handed = await handOffTaskSpace(input.task_id);
    marker({state:'awaiting_login', task_id:input.task_id, handoff_completed:handed.done === true});
    return;
  }
  await cdp('Browser.setDownloadBehavior', {behavior:'allow', downloadPath:input.download_dir, eventsEnabled:true});
  const search = await searchInput();
  if (!search) throw new Error('未找到邮箱搜索框；请确认已进入收件箱');
  await fillInput(search, '简历 应聘 ' + input.job_title);
  await pressKey('ENTER');
  await pause(3);
  const messages = await rows();
  const reviewed = [];
  for (const message of messages) {
    try {
      await click(message.selector);
      await pause(2);
      const proof = await evidence();
      if (!proof.intent || !proof.job) reviewed.push({status:'skipped_intent_uncertain'});
      else if (!proof.attachments.length) reviewed.push({status:'skipped_no_resume_attachment'});
      else {
        for (const attachment of proof.attachments) {
          await click(attachment.selector);
          await pause(2);
        }
        reviewed.push({status:'download_requested', attachments:proof.attachments.length});
      }
      await pressKey('ALT+LEFT');
      await pause(2);
    } catch (error) {
      reviewed.push({status:'skipped_ui_changed'});
      await pressKey('ALT+LEFT').catch(() => {});
      await pause(1);
    }
  }
  let stable = 0;
  let prior = '';
  for (let i=0;i<30 && stable<3;i++) {
    const current = fs.readdirSync(input.download_dir).sort().join('\\n');
    stable = current === prior && !/\\.crdownload$|\\.tmp$|\\.download$/im.test(current) ? stable + 1 : 0;
    prior = current;
    await pause(1);
  }
  const files = fs.readdirSync(input.download_dir)
    .filter(name => /\\.(pdf|docx?)$/i.test(name))
    .map(name => path.join(input.download_dir, name));
  marker({state:'collected', task_id:input.task_id, searched:messages.length, reviewed, files});
  await completeTaskSpace(input.task_id, {keep:false});
}

const operation = input.operation === 'start' ? start : input.operation === 'stop' ? stop : resume;
operation().catch(error => {
  marker({state:'failed', task_id:input.task_id || null, reason:String(error && error.message || error)});
  process.exitCode = 1;
});
