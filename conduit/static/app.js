const $ = id => document.getElementById(id);
let kind = 'website', current = null, apps = [], health = {}, pollTimer = null, taskTimer = null, runningJob = null;
let compileTimer = null;
const text = (tag, value, className) => { const el = document.createElement(tag); el.textContent = value; if (className) el.className = className; return el; };
async function api(path, body) {
  const response = await fetch('/api/' + path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Conduit-Local': '1'}, body: JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) { const error = new Error(typeof value.detail === 'string' ? value.detail : value.error || JSON.stringify(value.detail)); error.status = response.status; throw error; }
  return value;
}
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; setTimeout(() => $('toast').hidden = true, 4500); }
function notice(message, error = false) { $('detail-notice').textContent = message; $('detail-notice').className = 'notice' + (error ? ' error' : ''); $('detail-notice').hidden = false; }
async function selectKind(value) {
  kind = value;
  const desktop = kind === 'desktop';
  $('web-tab').classList.toggle('active', !desktop); $('desktop-tab').classList.toggle('active', desktop);
  $('web-tab').setAttribute('aria-selected', !desktop); $('desktop-tab').setAttribute('aria-selected', desktop);
  $('target-label').textContent = desktop ? 'App name or executable path' : 'Website address';
  $('target-icon').textContent = desktop ? '▣' : '↗';
  $('target').placeholder = desktop ? 'Microsoft Word or C:\\…\\app.exe' : 'https://excalidraw.com';
  $('target').value = ''; $('web-examples').hidden = desktop; $('web-options').hidden = desktop; $('app-picker').hidden = !desktop;
  if (desktop && !apps.length) {
    try { const result = await api('apps'); apps = result.apps; renderApps(); }
    catch (error) { toast(error.message); }
  }
}
function renderApps() {
  const search = $('app-search').value.toLowerCase(); $('apps').replaceChildren();
  for (const app of apps.filter(a => a.name.toLowerCase().includes(search))) { const option = text('option', app.name); option.value = app.id; $('apps').append(option); }
}
$('web-tab').onclick = () => selectKind('website'); $('desktop-tab').onclick = () => selectKind('desktop');
$('app-search').oninput = renderApps; $('apps').onchange = () => $('target').value = $('apps').value;
document.querySelectorAll('[data-url]').forEach(button => button.onclick = () => { $('target').value = button.dataset.url; $('private').checked = false; });
$('demo').onclick = () => { $('target').value = location.origin + '/playground'; $('private').checked = true; $('name').value = 'Creative playground'; };

$('build-form').onsubmit = async event => {
  event.preventDefault(); $('build-button').disabled = true; $('progress').hidden = false; $('progress').className = 'progress'; $('progress').textContent = 'Preparing…';
  try {
    const result = await api('build', {target: $('target').value.trim(), kind, name: $('name').value.trim(),
      tasks: $('tasks').value.split('\n').map(s => s.trim()).filter(Boolean), notes: $('notes').value,
      spec_url: kind === 'website' && $('spec').value.trim() || null,
      allowed_origins: kind === 'website' ? $('origins').value.split(',').map(s => s.trim()).filter(Boolean) : [],
      allow_private: kind === 'website' && $('private').checked});
    poll(result.job_id);
  } catch (error) { buildError(error.message); }
};
function buildError(message) { $('progress').textContent = message; $('progress').className = 'progress error'; $('build-button').disabled = false; }
async function poll(jobId) {
  try {
    const job = await api('jobs/' + jobId); $('progress').replaceChildren();
    for (const [i, message] of job.messages.entries()) { const line = text('div', message); if (i === job.messages.length - 1 && job.status !== 'complete') line.prepend(text('span', '', 'live')); $('progress').append(line); }
    if (job.status === 'complete') { $('progress').append(text('div', '✓ Connector generated. Explore its tools below.')); $('build-button').disabled = false; await listConnectors(); await showConnector(job.connector_id); }
    else if (job.status === 'error') buildError(job.error);
    else pollTimer = setTimeout(() => poll(jobId), 1100);
  } catch (error) { buildError(error.message); }
}
async function listConnectors() {
  const connectors = await api('connectors'); $('connection-count').textContent = connectors.length; $('empty').hidden = !!connectors.length; $('connectors').replaceChildren();
  for (const connector of connectors) {
    const button = document.createElement('button'); button.className = 'connection';
    button.append(text('span', connector.kind === 'desktop' ? '▣' : '↗', 'connection-symbol'));
    const copy = document.createElement('span'); copy.className = 'connection-copy'; copy.append(text('strong', connector.name), text('small', `${connector.tool_count} tools · ${connector.kind === 'website' ? new URL(connector.target).hostname : 'Windows app'}`));
    button.append(copy, text('span', '↗', 'arrow')); button.onclick = () => showConnector(connector.id).catch(e => toast(e.message)); $('connectors').append(button);
  }
}
function sample(schema) {
  const result = {};
  for (const [key, value] of Object.entries(schema.properties || {})) {
    if (!(schema.required || []).includes(key)) continue;
    result[key] = value.enum?.[0] ?? (value.type === 'array' ? [{x: 400, y: 350}, {x: 650, y: 500}] : value.type === 'number' || value.type === 'integer' ? 0 : value.type === 'boolean' ? false : value.type === 'object' ? {} : '');
  }
  return result;
}
async function showConnector(id, scroll = true) {
  clearTimeout(compileTimer);
  if (runningJob && current && id !== current.id) { toast('Finish or stop the current task before switching connections.'); return; }
  if (current?.id !== id) { $('result-area').hidden = true; $('screen').removeAttribute('src'); $('task-progress').hidden = true; $('task-request').value = ''; }
  current = await api('connectors/' + id); $('detail').hidden = false; $('detail-name').textContent = current.name; $('detail-target').textContent = current.target;
  $('config').textContent = clientConfig(); $('export').href = '/api/connectors/' + current.id + '/export'; $('tools-count').textContent = current.tools.length;
  $('login').hidden = current.kind === 'desktop'; $('refresh').hidden = current.kind === 'desktop'; $('finish-login').hidden = true; $('detail-notice').hidden = true;
  $('tool-list').replaceChildren();
  for (const tool of [...current.tools].sort((a,b) => Number(b.adapter === 'program') - Number(a.adapter === 'program'))) {
    const card = document.createElement('div'); card.className = 'tool'; const head = document.createElement('div'); head.className = 'tool-head';
    head.append(text('span', tool.name, 'tool-name'), text('span', tool.adapter === 'program' ? 'VERIFIED · ' + tool.execution_mode : tool.adapter === 'agent' ? 'VISION FALLBACK' : tool.read_only ? 'READ' : 'ACTION', 'badge' + (tool.read_only ? ' read' : '')));
    card.append(head, text('p', tool.description));
    const details = document.createElement('details'); details.append(text('summary', 'Try this tool'));
    const input = document.createElement('textarea'); input.value = JSON.stringify(sample(tool.input_schema), null, 2); input.setAttribute('aria-label', tool.name + ' arguments');
    const run = text('button', 'Run tool ↗', 'run-tool'); const connectorId = id;
    run.onclick = async () => { run.disabled = true; try { const {result} = await api('connectors/' + connectorId + '/call', {tool: tool.name, arguments: JSON.parse(input.value)}); showResult(result); } catch (error) { notice(error.message, true); } finally { run.disabled = false; } };
    details.append(input, run); card.append(details); $('tool-list').append(card);
  }
  $('findings').replaceChildren();
  for (const resource of current.capabilities || []) {
    const coverage = ['create','read','update','delete'].map(v => `${v}: ${resource.operations?.[v] ? 'passed' : 'unresolved'}`).join(' · ');
    const box = text('div', `${resource.label || resource.resource} — ${resource.status} (${resource.mode || 'unresolved'}). ${coverage}`, 'finding');
    $('findings').append(box);
  }
  if (current.compilation?.message) $('findings').append(text('p', current.compilation.message));
  for (const finding of current.findings) {
    const box = document.createElement('div'); box.className = 'finding';
    const labels = {page: 'Interface explored', http_page: 'Page reached', openapi: 'Optional API found', strategy: 'Interface-based connector', installed_app: 'Installed app found', accessibility: 'Windows controls found', session_refresh: 'Signed-in interface explored'};
    box.append(text('strong', labels[finding.type] || finding.type));
    box.append(text('span', finding.message || finding.title || finding.name || `${finding.operations ?? finding.controls ?? ''} capabilities`));
    if (finding.canvases) box.append(text('div', `${finding.canvases} canvas surface(s) · drawing controls included`));
    $('findings').append(box);
  }
  $('warnings').replaceChildren(...current.warnings.map(warning => text('div', warning, 'warning')));
  const modelMessage = health.model_configured ? (health.model_backend === 'codex' ? `Uses ${health.model} through your existing Codex sign-in. No separate API key needed. Running tasks uses your model allowance.` : `Uses ${health.model} at your configured model endpoint.`) : 'Sign in to the Codex CLI, or configure a vision model in .env, to run tasks here. Interface tools also work directly with your MCP client’s AI.';
  $('model-status').textContent = modelMessage + ' Screenshots and page text are sent to that model while a task runs.';
  $('runner-model').textContent = modelMessage;
  $('refresh').textContent = 'Learn tools';
  if (current.compile_job_id) watchCompilation(id, current.compile_job_id);
  if (scroll) $('detail').scrollIntoView({behavior: 'smooth', block: 'start'});
}
function showResult(result) {
  $('result-area').hidden = false; $('screen').hidden = !result.image_base64;
  if (result.image_base64) $('screen').src = `data:${result.mime_type};base64,${result.image_base64}`;
  const copy = {...result}; delete copy.image_base64; $('result').textContent = JSON.stringify(copy, null, 2);
  $('detail-notice').hidden = true;
}
async function quickAction(suffix) { if (!current) return; const prefix = current.kind === 'website' ? 'browser' : 'desktop'; try { const {result} = await api(`connectors/${current.id}/call`, {tool: `${prefix}_${suffix}`, arguments: {}}); showResult(result); } catch (error) { notice(error.message, true); } }
$('inspect').onclick = () => quickAction('snapshot'); $('see-screen').onclick = () => quickAction('screenshot');
$('clear-result').onclick = () => { $('result-area').hidden = true; $('screen').removeAttribute('src'); };
$('close-detail').onclick = () => $('detail').hidden = true;
function clientConfig() { return $('client-format').value === 'codex' ? current.codex_config || 'Update this connector to get Codex settings.' : JSON.stringify($('client-format').value === 'universal' ? current.universal_config : current.client_config, null, 2); }
$('client-format').onchange = () => { if (current) $('config').textContent = clientConfig(); };
$('copy-config').onclick = async () => { try { await api(`connectors/${current.id}/release`, {}); } catch(error) { notice('Could not release the preview: ' + error.message, true); return; } try { await navigator.clipboard.writeText(clientConfig()); toast('MCP settings copied. Preview released for your AI client.'); } catch { notice('Preview released. Copy the settings from the MCP connection settings panel below.'); } };
$('release').onclick = async () => { try { const result = await api(`connectors/${current.id}/release`, {}); notice(result.message); } catch(error) { notice(error.message, true); } };
$('login').onclick = async () => { $('login').disabled = true; try { const result = await api(`connectors/${current.id}/login/start`, {}); notice(result.message); $('finish-login').hidden = false; } catch (error) { notice(error.message, true); } finally { $('login').disabled = false; } };
$('finish-login').onclick = async () => { $('finish-login').disabled = true; try { const result = await api(`connectors/${current.id}/login/finish`, {}); await showConnector(current.id, false); notice(result.message); } catch (error) { notice(error.message, true); } finally { $('finish-login').disabled = false; } };
$('refresh').onclick = async () => { try { const result = await api(`connectors/${current.id}/compile`, {}); watchCompilation(current.id, result.job_id); } catch (error) { notice(error.message, true); } };
async function watchCompilation(id, jobId) {
  if (current?.id !== id) return;
  clearTimeout(compileTimer);
  try {
    const job = await api('jobs/' + jobId);
    if (current?.id !== id) return;
    if (['complete','error','cancelled'].includes(job.status)) {
      await showConnector(id, false);
      notice(job.error || `Tool learning: ${job.result?.status || job.status}. ${job.result?.message || ''}`, job.status !== 'complete');
      return;
    }
    notice(job.messages.at(-1) || 'Learning tools…');
  } catch (error) { notice(error.message, true); }
  compileTimer = setTimeout(() => watchCompilation(id, jobId), 1500);
}
Promise.all([api('health').then(value => { health = value; }), listConnectors()]).catch(error => toast(error.message));
$('task-form').onsubmit = async event => {
  event.preventDefault(); if (!current || runningJob) return;
  taskControls(true); $('task-progress').hidden = false; $('task-progress').textContent = 'Starting…'; $('task-progress').className = 'progress';
  try { const response = await api(`connectors/${current.id}/run`, {tool:'task_do', arguments:{details:$('task-request').value,max_steps:18}}); runningJob = response.job_id; $('stop-task').hidden = false; pollTask(); }
  catch(error) { finishTask(error.message, true); }
};
function taskControls(busy) { document.querySelectorAll('#detail .toolbar button, #detail .run-tool, #run-task, #task-request').forEach(control => control.disabled = busy); }
function finishTask(message, error=false) { runningJob = null; taskControls(false); $('stop-task').hidden = true; $('task-progress').textContent = message; $('task-progress').className = 'progress' + (error ? ' error' : ''); }
async function pollTask() {
  if (!runningJob) return;
  try {
    const job = await api('jobs/' + runningJob); $('task-progress').replaceChildren(...job.messages.slice(-3).map(m=>text('div',m)));
    if (job.status === 'complete') { const result = job.result; finishTask(result.summary + (result.evidence ? ' Evidence: ' + result.evidence : ''), result.status !== 'model_reported_complete'); showResult(result); }
    else if (job.status === 'error' || job.status === 'cancelled') finishTask(job.error, true);
    else taskTimer = setTimeout(pollTask, 1200);
  } catch(error) { if(error.status === 404) finishTask('The server no longer has this task. Check the app before starting again.', true); else { $('task-progress').textContent = 'Connection interrupted. The task may still be running. Reconnecting…'; taskTimer = setTimeout(pollTask, 2500); } }
}
$('stop-task').onclick = async () => { if (!runningJob) return; $('stop-task').disabled = true; try { await api('jobs/' + runningJob + '/cancel', {}); } catch(error) { notice('Could not send the stop request: ' + error.message, true); } finally { $('stop-task').disabled = false; } };
window.addEventListener('beforeunload', () => { clearTimeout(pollTimer); clearTimeout(taskTimer); clearTimeout(compileTimer); });
const universalOption = text('option', 'Universal MCP (keep Conduit running)'); universalOption.value = 'universal'; $('client-format').append(universalOption);
