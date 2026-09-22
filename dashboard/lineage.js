// SPDX-License-Identifier: Apache-2.0
// Prompt provenance is evidence, not inferred reasoning or reconstructed history.
function observedModels(task) {
  const models = task.actual_models?.length ? task.actual_models : task.actual_model ? [task.actual_model] : [];
  return models.length ? models.join(', ') : '미관측 — 요청 모델과 구분';
}

function routingGraphHtml(run) {
  const tasks = run.tasks || [];
  const positions = new Map();
  const depths = new Map();
  const depth = (task, visiting = new Set()) => {
    if (visiting.has(task.id)) return 0;
    if (depths.has(task.id)) return depths.get(task.id);
    const next = new Set(visiting).add(task.id);
    const parents = (task.depends_on || []).map(id => tasks.find(t => t.id === id)).filter(Boolean);
    const value = parents.length ? 1 + Math.max(...parents.map(parent => depth(parent, next))) : 0;
    depths.set(task.id, value);
    return value;
  };
  tasks.forEach((task, i) => positions.set(task.id, {x: 240 + depth(task) * 390, y: 30 + i * 190}));
  const width = Math.max(640, ...[...positions.values()].map(p => p.x + 360));
  const height = Math.max(160, tasks.length * 190 + 20);
  const edges = [], nodes = [];
  const text = (x, y, value, cls = '') => `<text x="${x}" y="${y}" class="${cls}">${escapeHtml(value)}</text>`;
  const wrap = value => String(value).match(/.{1,35}/gu) || [''];
  for (const task of tasks) {
    const p = positions.get(task.id);
    const parents = task.depends_on?.length ? task.depends_on : [null];
    for (const id of parents) {
      const from = id === null ? {x: 190, y: 90} : positions.has(id) ? {x: positions.get(id).x + 340, y: positions.get(id).y + 75} : null;
      if (!from) continue;
      const endY = p.y + 75, mid = (from.x + p.x) / 2;
      edges.push(`<path d="M${from.x} ${from.y} H${mid} V${endY} H${p.x}"/><path d="M${p.x-7} ${endY-4} L${p.x} ${endY} L${p.x-7} ${endY+4}"/>`);
    }
    const execution = task.execution_provider || '미실행 / 미기록';
    const model = observedModels(task);
    nodes.push(`<g class="lineage-node ${task.fallback ? 'lineage-fallback' : ''}"><title>${escapeHtml(task.title)} | ${escapeHtml(model)}</title><rect x="${p.x}" y="${p.y}" width="340" height="160" rx="6"/>${text(p.x+12,p.y+24,task.id,'lineage-node-title')}${text(p.x+12,p.y+48,`Jev: ${task.selected_by_jev || '미기록'}`)}${text(p.x+12,p.y+70,`${task.fallback ? 'Fallback → ' : task.execution_override ? 'Override → ' : '실행 → '}${execution} CLI`)}${wrap(model).slice(0,2).map((line,i)=>text(p.x+12,p.y+94+i*18,line)).join('')}${text(p.x+12,p.y+142,`${task.status || '대기'} · ${task.effort || 'effort 미기록'}`)}</g>`);
  }
  return `<div class="lineage-diagram"><svg role="img" aria-label="사용자 요청에서 의존 작업 및 실행 CLI와 관측 모델로 이어지는 라우팅 그래프" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}"><g class="lineage-edges">${edges.join('')}</g><g class="lineage-node"><rect x="10" y="45" width="180" height="90" rx="6"/>${text(22,74,'사용자 지시','lineage-node-title')}${text(22,98,run.user_prompt ? '원문 기록됨' : '원문 미기록')}${text(22,119,run.origin_provider || '부모 CLI 미기록')}</g>${nodes.join('')}</svg></div>`;
}

function lineageHtml(run) {
  const block = (title, value, note = '') => `<section class="prompt-stage"><h4>${title}</h4>${note ? `<p>${escapeHtml(note)}</p>` : ''}<pre>${escapeHtml(value || '미기록 — 과거 지시를 추정하여 복원하지 않습니다.')}</pre></section>`;
  const tasks = (run.tasks || []).map(task => `<article class="lineage-task">
    <h3>${escapeHtml(task.id)} · ${escapeHtml(task.title)}</h3>
    <p>의존 작업: ${escapeHtml(task.depends_on?.join(', ') || '없음 — 독립 작업')} · ${escapeHtml(task.category)} / ${escapeHtml(task.difficulty)}</p>
    <div class="lineage-route"><span>Jev 선택 <b>${escapeHtml(task.selected_by_jev || '미기록')}</b> (${pct(task.confidence)})</span><span aria-hidden="true">→</span><span>${task.fallback ? 'Fallback' : task.execution_override ? 'Override' : '실행 대상'} <b>${escapeHtml(task.execution_provider || '미실행 / 미기록')} CLI</b></span><span aria-hidden="true">→</span><span>관측 모델 <b>${escapeHtml(observedModels(task))}</b></span></div>
    ${task.fallback ? `<p class="notice">Fallback 사유: ${escapeHtml(task.fallback_reason || '미기록')}</p>` : ''}
    <dl class="lineage-metadata"><dt>요청 모델</dt><dd>${escapeHtml(task.requested_model || 'CLI 기본값 — 정확한 요청 ID 미기록')}</dd><dt>관측 근거</dt><dd>${escapeHtml(task.model_evidence_source || (task.actual_models?.length ? 'run.json actual_models / actual_model' : 'CLI가 모델 ID를 보고하지 않음'))}</dd><dt>세션 / 프로세스</dt><dd>${escapeHtml(task.session_ids?.join(', ') || '세션 미기록')} / ${escapeHtml(task.process_id ?? 'PID 미기록')}</dd></dl>
    <div class="prompt-stages">${block('분할·변환된 작업 프롬프트',task.prompt,'부모가 작성한 manifest의 task.prompt')}${block('의존 결과가 합쳐진 실행 프롬프트',task.expanded_prompt,task.expanded_prompt_truncated ? '표시 길이 제한으로 일부 생략' : '작업 프롬프트 + 선행 작업 결과')}${block('실제 CLI에 전달된 프롬프트',task.submitted_prompt,(task.submitted_prompt_source || '전달문 미기록') + (task.submitted_prompt_truncated ? ' · 표시 길이 제한으로 일부 생략' : ''))}</div>
    ${task.submitted_prompt_sha256 ? `<p class="prompt-hash">전달문 SHA-256: ${escapeHtml(task.submitted_prompt_sha256)}</p>` : ''}
  </article>`).join('');
  return `<section class="lineage"><h3>지시 → 작업 분할 → 라우팅 → 실행 증거</h3><p>작업 분할은 부모 에이전트가 수행합니다. Jev의 선택과 실제 실행 대상, 요청 모델과 관측 모델을 별도로 표시합니다.</p>${block('사용자가 작성한 지시 원문',run.user_prompt,`부모: ${run.parent_agent || run.origin_provider || '미기록'} · 당시 fallback 임계값: ${run.confidence_threshold == null ? '미기록' : pct(run.confidence_threshold)}`)}${routingGraphHtml(run)}${tasks}</section>`;
}
