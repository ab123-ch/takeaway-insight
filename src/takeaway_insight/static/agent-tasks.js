"use strict";
window.TakeawayAgentTasks = ({api, escape, field, openDialog, toast, main, loadPage, getLoadId}) => {
  let state = {tasks: [], runs: [], info: {}};
  const $ = selector => document.querySelector(selector);
  const time = value => value ? new Date(value).toLocaleString("zh-CN", {timeZone: "Asia/Shanghai"}) : "—";
  const labels = {queued: "等待 worker", running: "Agent 执行中", completed: "进程已结束", failed: "执行失败", interrupted: "已中断"};
  async function load(current) {
    const [tasks, runs, info] = await Promise.all([api("/api/agent-tasks"), api("/api/agent-runs"), api("/api/agent-info")]);
    if (current !== getLoadId()) return;
    state = {tasks: tasks.items, runs: runs.items, info};
    main.innerHTML = `<section class="panel"><div class="panel-header"><div><h2>本地自动执行任务</h2><p>代码只启动Agent、传递提示词和记录进程结果；采集、入库和核对由Agent执行。</p></div><button class="button" data-action="new-agent-task">新增任务</button></div><div class="panel-body guide-prose"><p>完整权限 · 关闭沙箱 · 工具自动执行 · 不限制Agent运行时长或工具选择。</p><p>agy：${escape(info.executables.antigravity || "未找到，请安装或配置可执行路径")}<br>dsh：${escape(info.executables.dsh || "未找到，请安装或配置可执行路径")}</p><p>自动执行需要本机运行 <code>uv run takeaway worker</code>。工程目录：<code>${escape(info.project_dir)}</code></p></div>${tasks.items.length ? `<div class="table-wrap"><table><thead><tr><th>任务</th><th>执行器</th><th>自动执行</th><th>下次触发</th><th>操作</th></tr></thead><tbody>${tasks.items.map(task => `<tr><td><strong>${escape(task.name)}</strong><div class="secondary-text">#${task.id}</div></td><td>${escape(task.runner)}<div class="secondary-text">${escape(task.arguments.join(" "))}</div></td><td>${task.enabled ? `每 ${escape(task.interval_minutes)} 分钟` : "未启用"}</td><td>${task.enabled ? (task.next_run_at ? time(task.next_run_at) : "worker启动后立即触发") : "—"}</td><td><button class="text-button" data-action="edit-agent-task" data-id="${task.id}">编辑</button> <button class="button secondary compact" data-action="run-agent-task" data-id="${task.id}">立即执行</button></td></tr>`).join("")}</tbody></table></div>` : '<div class="panel-body">添加任务，选择本地agy或dsh，填写提示词和执行间隔。</div>'}</section><section class="panel" style="margin-top:20px"><div class="panel-header"><div><h2>Agent 运行记录</h2><p>进程退出码仅表示执行器状态；数据核对结果请阅读Agent报告。</p></div><button class="button secondary compact" data-action="refresh">刷新</button></div>${runs.items.length ? `<div class="table-wrap"><table><thead><tr><th>任务 / 来源</th><th>状态</th><th>开始 / 结束</th><th>退出码</th><th>报告</th></tr></thead><tbody>${runs.items.map(run => `<tr><td>${escape(run.task_name)}<div class="secondary-text">${run.trigger === "schedule" ? "定时触发" : "手动触发"} · ${escape(run.runner)}</div></td><td>${escape(labels[run.status] || run.status)}${run.error ? `<p class="sync-error-cell">${escape(run.error)}</p>` : ""}</td><td>${time(run.started_at || run.created_at)}<div class="secondary-text">${time(run.finished_at)}</div></td><td>${run.exit_code === null ? "—" : escape(run.exit_code)}</td><td><button class="text-button" data-action="agent-log" data-id="${escape(run.id)}">查看日志与核对报告</button></td></tr>`).join("")}</tbody></table></div>` : '<div class="panel-body">尚无运行记录。启用定时或点击立即执行，worker会启动所选Agent。</div>'}</section>`;
  }
  function edit(id) {
    const task = state.tasks.find(item => item.id === Number(id));
    openDialog(task ? "编辑 Agent 任务" : "新增 Agent 任务", "本地原生执行", `<form id="agent-task-form">${field("任务名称", "name", task?.name || "三平台自动采集", "required")}<div class="field-grid"><div class="field"><label for="agent-runner">执行器</label><select id="agent-runner" name="runner"><option value="antigravity" ${!task || task.runner === "antigravity" ? "selected" : ""}>Antigravity · agy</option><option value="dsh" ${task?.runner === "dsh" ? "selected" : ""}>DeepSeek Harness · dsh</option><option value="custom" ${task?.runner === "custom" ? "selected" : ""}>自定义命令</option></select></div>${field("执行间隔（分钟）", "interval_minutes", task?.interval_minutes || "15", 'type="number" min="1" step="1"')}</div><label class="checkbox-label"><input type="checkbox" name="enabled" ${(!task || task.enabled) ? "checked" : ""}>启用定时自动执行</label><div class="field"><label for="agent-arguments">指定Agent / 附加参数（JSON数组）</label><textarea id="agent-arguments" name="arguments" rows="3">${escape(JSON.stringify(task?.arguments || []))}</textarea><small>agy示例：["--agent", "你的Agent名称"]；dsh示例：["--patch", "/配置文件路径"]。</small></div><div class="field"><label for="agent-command">自定义命令（仅自定义执行器，JSON数组）</label><textarea id="agent-command" name="command" rows="3">${escape(JSON.stringify(task?.command || []))}</textarea><small>原样执行命令。{prompt}替换提示词，标准输入也会提供提示词。</small></div><div class="field"><label for="agent-variables">任务变量（JSON对象）</label><textarea id="agent-variables" name="variables" rows="3">${escape(JSON.stringify(task?.variables || {cdp_url: "http://127.0.0.1:9222"}, null, 2))}</textarea></div><div class="field"><label for="agent-prompt">提示词</label><textarea id="agent-prompt" name="prompt" rows="14" required>${escape(task?.prompt || state.info.default_prompt)}</textarea><small>支持 {{ today }}、{{ project_dir }}、{{ database_skill }}、{{ variables }} 等模板变量。</small></div><div class="form-actions"><button class="button secondary" type="button" data-action="close-dialog">取消</button><button class="button" type="submit">保存任务</button></div></form>`);
    $("#agent-task-form").addEventListener("submit", async event => {
      event.preventDefault();
      const form = event.currentTarget, button = form.querySelector("[type=submit]");
      button.disabled = true;
      try {
        const data = {name: form.elements.name.value, runner: form.elements.runner.value,
          arguments: JSON.parse(form.elements.arguments.value), command: JSON.parse(form.elements.command.value),
          variables: JSON.parse(form.elements.variables.value), prompt: form.elements.prompt.value,
          enabled: form.elements.enabled.checked, interval_minutes: form.elements.interval_minutes.value ? Number(form.elements.interval_minutes.value) : null};
        await api(task ? `/api/agent-tasks/${task.id}` : "/api/agent-tasks", {method: task ? "PUT" : "POST", body: data});
        $("#editor").close(); toast("Agent任务已保存，自动执行由worker负责。"); await loadPage();
      } catch (error) {
        let target = form.querySelector("[role=alert]");
        if (!target) { target = document.createElement("p"); target.setAttribute("role", "alert"); target.className = "notice error"; form.prepend(target); }
        target.textContent = error.message; target.scrollIntoView({block:"nearest"});
      } finally { button.disabled = false; }
    });
  }
  async function run(id) {
    await api(`/api/agent-tasks/${id}/run`, {method:"POST", body:{request:""}});
    toast("已排队，worker将启动本地Agent；结果请查看运行日志。");
    await loadPage();
  }
  async function log(id) {
    const response = await fetch(`/api/agent-runs/${encodeURIComponent(id)}/log`, {credentials:"same-origin"});
    if (!response.ok) throw new Error("读取日志失败，请检查登录状态。");
    const content = await response.text();
    openDialog("Agent日志与核对报告", "原生进程输出", `<p class="field-hint">进程结束不代表数据已经核对通过，请查看实际报告。</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${escape(content)}</pre><div class="form-actions"><button type="button" class="button secondary" data-action="close-dialog">关闭</button></div>`);
  }
  return {load,edit,run,log};
};
