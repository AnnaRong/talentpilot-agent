import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { SpreadsheetFile, Workbook } from '@oai/artifact-tool';

const [input, outputDirectory] = process.argv.slice(2);
if (!input || !outputDirectory) throw new Error('Usage: build_verified_workbook.mjs capture.json output-directory');
const data = JSON.parse(await fs.readFile(input, 'utf8'));
if (data.schema_version !== 'talentpilot-ledger/2') throw new Error('Unsupported capture schema');
const wb = Workbook.create();
const positions = [...data.positions].sort((a, b) => a.department.localeCompare(b.department, 'zh-CN') || a.job_id.localeCompare(b.job_id));
const byJob = new Map(positions.map(p => [p.job_id, p]));
const interviews = data.interviews;
const apps = data.applications;
const missing = '未采集';
const unique = values => [...new Set(values.filter(Boolean))];
const safe = value => typeof value === 'string' && /^[=+@]/.test(value) ? `'${value}` : value;
const sheets = [];
const columns = n => {
  let result = '';
  for (let i = n; i > 0; i = Math.floor((i - 1) / 26)) result = String.fromCharCode(65 + (i - 1) % 26) + result;
  return result;
};
function table(name, headers, rows, widths) {
  const sheet = wb.worksheets.add(name);
  sheet.showGridLines = false;
  sheet.getRangeByIndexes(0, 0, rows.length + 1, headers.length).values = [headers, ...rows].map(row => row.map(safe));
  const range = sheet.getRangeByIndexes(0, 0, rows.length + 1, headers.length);
  range.format.font = { name: 'Arial', size: 10, color: '#252A30' };
  range.format.verticalAlignment = 'center';
  range.format.rowHeight = 30;
  range.format.wrapText = true;
  sheet.getRangeByIndexes(0, 0, 1, headers.length).format = {
    fill: '#365247', font: { name: 'Arial', size: 10, bold: true, color: '#FFFFFF' },
    rowHeight: 44, horizontalAlignment: 'center', verticalAlignment: 'center', wrapText: true,
  };
  headers.forEach((_, index) => sheet.getRangeByIndexes(0, index, rows.length + 1, 1).format.columnWidth = widths?.[index] || 18);
  if (rows.length) {
    const nativeTable = sheet.tables.add(`A1:${columns(headers.length)}${rows.length + 1}`, true, `LedgerTable${sheets.length + 1}`);
    nativeTable.showFilterButton = true;
  }
  sheet.freezePanes.freezeRows(1);
  sheets.push({ name, rows: rows.length, columns: headers.length });
  return sheet;
}
const valid = interviews.filter(i => i.rate_eligible);
const individual = interviews.flatMap(i => i.interviewers.map(person => ({ ...person, interview: i })));
const source = 'iTalent授权只读页面；采集时间见完整性与总结';
const summary = [
  ['采集开始（北京时间）', new Date(new Date(data.run.started_at).getTime() + 8 * 3600 * 1000)],
  ['采集完成（北京时间）', new Date(new Date(data.run.completed_at).getTime() + 8 * 3600 * 1000)],
  ['核验账号', data.run.viewer_name + '（iTalent；未核验如流）'],
  ['岗位清单', `${positions.length}/${data.quality.coverage.positions.declared}；包含19个社招实习、8个校园招聘岗位`],
  ['社招实习申请关系', apps.length],
  ['社招实习候选人数', new Set(apps.map(a => a.candidate_id)).size],
  ['全部时间面试场次', interviews.length],
  ['已完成场次', interviews.filter(i => i.status === '已完成').length],
  ['未完成场次', interviews.filter(i => i.status === '未完成').length],
  ['已取消场次', interviews.filter(i => i.status === '已取消').length],
  ['已完成明确通过场次', valid.filter(i => i.overall_result === '通过').length],
  ['已完成明确淘汰场次', valid.filter(i => i.overall_result === '淘汰').length],
  ['个人评价条数', individual.length],
  ['含面评正文条数', data.quality.coverage.feedback.text_records],
  ['Offer进行中存量', apps.filter(a => a.current_stage === 'Offer' && a.current_status === '进行中').length],
  ['已发Offer数量', '未知：未采集逐条发送记录，不能用Offer阶段数量代替'],
  ['已入职存量', apps.filter(a => a.current_stage === '入职' && a.current_status === '已入职').length],
  ['统计口径', '面试通过率以场次综合结论计算，通过/(通过+淘汰)；仅已完成且预约时间不在未来的场次进入分母。'],
  ['关联口径', '申请+候选人+岗位ID关联；安排ID与详情面试ID不同，面评按同一申请、候选人、岗位、轮次、时间、面试官ID唯一匹配。'],
  ['筛选口径', '当前初筛/复筛淘汰为阶段存量，不能代表所有历史筛选结果。历史通过率未知。'],
  ['来源', source],
  ...data.quality.warnings.map((warning, index) => [`范围与缺口 ${index + 1}`, warning]),
];
const summarySheet = table('完整性与总结', ['项目', '结果'], summary, [28, 114]);
summarySheet.getRange('B2:B3').setNumberFormat('yyyy-mm-dd hh:mm:ss');
summarySheet.getRange(`A2:B${summary.length + 1}`).format.rowHeight = 42;

const mainHeaders = [
  '需求部门', '岗位', '岗位编号', '招聘负责人', '用人经理', '需求发布人', '申请数（当前范围）',
  '初筛进行中', '初筛淘汰存量', '复筛进行中', '复筛淘汰存量',
  '一面场次', '一面通过', '一面淘汰', '一面通过率',
  '二面场次', '二面通过', '二面淘汰', '二面通过率',
  '三面场次', '三面通过', '三面淘汰', '三面通过率',
  'Offer进行中', '已发Offer', '已入职存量', '实际一面面试官', '实际二面面试官', '实际三面面试官', '统计范围',
];
const mainRows = positions.map(p => {
  const collected = p.process_name === '百度社招实习生招聘流程';
  const ownApps = apps.filter(a => a.job_id === p.job_id);
  const ownInterviews = interviews.filter(i => i.job_id === p.job_id);
  const stage = (name, status) => collected ? ownApps.filter(a => a.current_stage === name && a.current_status === status).length : '未覆盖该流程';
  const counts = round => {
    const all = ownInterviews.filter(i => i.round === round);
    return [all.length, all.filter(i => i.rate_eligible && i.overall_result === '通过').length,
      all.filter(i => i.rate_eligible && i.overall_result === '淘汰').length, null];
  };
  return [
    p.department, p.title, p.job_id, p.recruiter, missing, missing,
    collected ? ownApps.length : '未覆盖该流程',
    stage('简历初筛', '进行中'), stage('简历初筛', '本轮淘汰'), stage('简历复筛', '进行中'), stage('简历复筛', '本轮淘汰'),
    ...counts('一面'), ...counts('二面'), ...counts('三面'),
    stage('Offer', '进行中'), '未知', stage('入职', '已入职'),
    ...['一面', '二面', '三面'].map(round => unique(ownInterviews.filter(i => i.round === round).flatMap(i => i.interviewers.map(e => e.name))).join('、') || '无已采集安排'),
    collected ? '社招实习当前申请；全部时间面试' : '校园流程：仅面试已采集，申请未覆盖',
  ];
});
const main = table('招聘台账', mainHeaders, mainRows, [25, 39, 14, 16, 14, 16, 18, 15, 16, 15, 16, 13, 13, 13, 14, 13, 13, 13, 14, 13, 13, 13, 14, 16, 14, 16, 38, 38, 38, 44]);
for (let n = 2; n <= mainRows.length + 1; n++) {
  for (const [dest, pass, reject] of [['O', 'M', 'N'], ['S', 'Q', 'R'], ['W', 'U', 'V']]) {
    main.getRange(`${dest}${n}`).formulas = [[`=IF(${pass}${n}+${reject}${n}=0,"",${pass}${n}/(${pass}${n}+${reject}${n}))`]];
    main.getRange(`${dest}${n}`).setNumberFormat('0.0%');
  }
}
main.getRange(`A2:AD${mainRows.length + 1}`).format.rowHeight = 90;
main.freezePanes.freezeColumns(3);

const byApp = new Map();
for (const i of interviews) {
  const key = [i.application_id, i.candidate_id, i.job_id].join('|');
  if (!byApp.has(key)) byApp.set(key, []);
  byApp.get(key).push(i);
}
const applicationRows = apps.map(a => {
  const own = byApp.get([a.application_id, a.candidate_id, a.job_id].join('|')) || [];
  return [
    a.job_id, byJob.get(a.job_id)?.department || '当前岗位清单外', a.candidate_name,
    a.application_id, a.candidate_id, a.job_uuid, a.current_stage, a.current_status,
    ...['一面', '二面', '三面'].map(round => own.filter(i => i.round === round).map(i => `${i.scheduled_at} ${i.status}/${i.overall_result}`).join('\n') || '无已采集安排'),
    a.entry_status || '未展示', a.status_changed_at, a.source_page,
  ];
});
const appSheet = table('候选人流程', ['岗位编号', '需求部门', '候选人', '申请ID', '候选人ID', '岗位UUID', '当前阶段', '当前状态', '一面证据', '二面证据', '三面证据', '源入职状态', '状态变化时间', '源页码'], applicationRows, [14, 25, 16, 40, 40, 40, 16, 16, 48, 48, 48, 16, 25, 12]);
appSheet.getRange(`I2:K${applicationRows.length + 1}`).format.wrapText = true;
appSheet.getRange(`A2:N${applicationRows.length + 1}`).format.rowHeight = 58;
appSheet.getRange(`M2:M${applicationRows.length + 1}`).setNumberFormat('yyyy-mm-dd hh:mm:ss');

const interviewRows = interviews.map(i => [
  i.job_id, i.job_label, i.candidate_name, i.application_id, i.candidate_id, i.interview_id,
  i.round, i.scheduled_at, i.status, i.overall_result, i.rate_eligible ? '是' : '否',
  i.organizer, i.interviewers.map(e => `${e.name}：${e.result}`).join('；'),
  i.in_application_scope ? '匹配社招实习申请' : '申请范围外：保留面试原始关系',
  i.in_current_job_scope ? '当前岗位' : '当前岗位清单外', i.source_page,
]);
const interviewSheet = table('面试明细', ['岗位编号', '源岗位名称', '候选人', '申请ID', '候选人ID', '面试安排ID', '轮次', '预约时间', '面试状态', '场次综合结论', '进入通过率分母', '安排人', '个人结果（独立）', '申请关联范围', '岗位关联范围', '源页码'], interviewRows, [14, 45, 16, 40, 40, 40, 12, 25, 16, 18, 18, 16, 38, 36, 26, 12]);
interviewSheet.getRange(`H2:H${interviewRows.length + 1}`).setNumberFormat('yyyy-mm-dd hh:mm:ss');

const feedbackRows = individual.map(e => {
  const i = e.interview;
  return [e.result === '通过' ? '通过' : e.result === '淘汰' ? '淘汰' : '未评价/其他',
    i.job_id, i.candidate_name, i.round, i.scheduled_at, e.name, e.result, e.detail_result || '未展示',
    e.feedback, '未采集', i.interview_id, i.application_id, i.candidate_id, String(e.interviewer_id),
    i.status, i.overall_result, e.feedback_collected ? '详情已核验' : e.detail_checked ? '详情已读，个人正文未返回' : '详情未采集',
    e.detail_interview_id || '未采集', e.join_method || '未关联'];
});
const feedbackSheet = table('面评明细', ['个人结果分类', '岗位编号', '候选人', '轮次', '面试时间', '面试官', '列表个人结论', '详情个人结论', '面评正文（脱敏原文）', '评价时间', '面试安排ID', '申请ID', '候选人ID', '面试官ID', '场次状态', '场次综合结论', '正文采集状态', '详情面试ID', '关联方式'], feedbackRows, [18, 14, 16, 12, 25, 16, 18, 18, 100, 18, 40, 40, 40, 18, 16, 18, 22, 40, 52]);
feedbackSheet.getRange(`E2:E${feedbackRows.length + 1}`).setNumberFormat('yyyy-mm-dd hh:mm:ss');
// Keep complete feedback in cells; size rows to content without truncating long source text.
feedbackRows.forEach((row, index) => {
  const lines = String(row[8] || '').split('\n').reduce((n, line) => n + Math.max(1, Math.ceil(line.length / 48)), 0);
  feedbackSheet.getRange(`A${index + 2}:Q${index + 2}`).format.rowHeight = Math.min(409, Math.max(32, lines * 14 + 8));
});

const offerRows = apps.filter(a => ['拟录用', 'Offer', '入职'].includes(a.current_stage)).map(a => [
  a.job_id, a.candidate_name, a.application_id, a.candidate_id, a.current_stage, a.current_status,
  a.entry_status, '未知', missing, missing, '仅阶段状态，不证明Offer已发送',
]);
table('Offer与入职', ['岗位编号', '候选人', '申请ID', '候选人ID', '当前阶段', '当前状态', '源入职状态', '是否已发Offer', 'OfferID', '发送时间', '证据边界'], offerRows, [14, 16, 40, 40, 16, 18, 18, 20, 20, 20, 48]);

const inventoryRows = Object.entries(data.quality.stage_counts).map(([key, value]) => [...key.split('|'), value, '社招实习流程；招聘中职位；全部简历']);
table('阶段存量', ['当前阶段', '当前状态', '申请数', '范围'], inventoryRows, [18, 22, 16, 64]);
const pageRows = Object.entries(data.quality.pages).flatMap(([type, pages]) => pages.map(p => [type, p.page, p.count, p.total]));
table('分页校验', ['数据集', '页码', '本页条数', '接口声明总数'], pageRows, [22, 16, 18, 22]);

await fs.mkdir(outputDirectory, { recursive: true });
wb.recalculate();
const inspected = await wb.inspect({ kind: 'table', range: '招聘台账!A1:O5', include: 'values,formulas', tableMaxRows: 5, tableMaxCols: 15, maxChars: 5000 });
await fs.writeFile(path.join(outputDirectory, 'verification.ndjson'), inspected.ndjson);
const errors = await wb.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!|#CALC!', options: { useRegex: true, maxResults: 100 }, maxChars: 3000 });
await fs.writeFile(path.join(outputDirectory, 'formula-errors.ndjson'), errors.ndjson);
for (const spec of sheets) {
  const endCol = Math.min(spec.columns, spec.name === '面评明细' ? 9 : 7);
  const endRow = Math.min(spec.rows + 1, spec.name === '面评明细' ? 4 : 7);
  const preview = await wb.render({ sheetName: spec.name, range: `A1:${columns(endCol)}${endRow}`, scale: 1.2, format: 'png' });
  await fs.writeFile(path.join(outputDirectory, `${spec.name}-preview.png`), new Uint8Array(await preview.arrayBuffer()));
}
const output = path.join(outputDirectory, '招聘台账-重新核对-20260921.xlsx');
await (await SpreadsheetFile.exportXlsx(wb)).save(output);
await fs.writeFile(path.join(outputDirectory, 'workbook-manifest.json'), JSON.stringify({
  output, sheets, source: input, generated_at: new Date().toISOString(),
  source_sha256: createHash('sha256').update(await fs.readFile(input)).digest('hex'),
}, null, 2));
console.log(JSON.stringify({ output, sheets }, null, 2));
