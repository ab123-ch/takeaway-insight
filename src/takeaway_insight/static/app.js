"use strict";
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) =>
    Array.from(root.querySelectorAll(selector));
  const escape = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (char) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[char],
    );
  const paths = {
    overview:
      '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="11" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="18" width="7" height="3" rx="1"/>',
    orders:
      '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Z"/><path d="M9 7h6M9 11h6M9 15h3"/>',
    sales: '<path d="M4 20V10m8 10V4m8 16v-8M2 20h20"/>',
    split:
      '<path d="M12 3v6m0 0-7 5v6m7-11 7 5v6M9 5l3-3 3 3M2 17l3 3 3-3m8 0 3 3 3-3"/>',
    store:
      '<path d="M4 9v12h16V9M3 9l2-6h14l2 6M3 9c0 4 6 4 6 0 0 4 6 4 6 0 0 4 6 4 6 0M9 21v-7h6v7"/>',
    sync: '<path d="M20 7a8 8 0 0 0-14-2L3 8m0-5v5h5m-4 9a8 8 0 0 0 14 2l3-3m0 5v-5h-5"/>',
    mcp: '<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-14-2 18"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
    plus: '<path d="M12 4v16M4 12h16"/>',
    chevron: '<path d="m9 5 7 7-7 7"/>',
    download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    edit: '<path d="m14 4 6 6M4 20l5-1L21 7a2 2 0 0 0-4-4L5 15l-1 5Z"/>',
    copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
    money: '<path d="m6 3 6 8 6-8M5 11h14M5 16h14m-7-5v10"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
  };
  function icon(name) {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.info}</svg>`;
  }
  function icons(root = document) {
    $$("[data-icon]", root).forEach((el) => {
      el.innerHTML = icon(el.dataset.icon);
    });
  }
  const page = document.body.dataset.page;
  if (!page) return;
  const titles = {
    agents: ["Agent 定时任务", "定时调用本地 agy / dsh，由 Agent 通过 CDP 采集、入库和核对。"],
    dashboard: ["经营概览", "从订单到单品，看清今天的生意。"],
    orders: ["订单明细", "保留原始订单，套餐的每个子项都有来处。"],
    sales: ["商品销量", "按商品与单位统计，为下一次备货提供依据。"],
    rules: ["套餐拆分", "把平台的套餐名称，变成门店熟悉的商品明细。"],
    merchants: ["门店接入", "每家门店独立授权、独立同步，集中查看。"],
    syncs: ["同步记录", "查看每次拉取的结果，补齐需要对账的营业日。"],
    "mcp-guide": [
      "MCP 分析接入",
      "让 dsh 读取经营数据，再结合库存与配方分析备货。",
    ],
  };
  const state = {
    merchants: [],
    session: {},
    page: 1,
    orderStatus: "",
    query: "",
    confirmed: false,
    rules: [],
    timer: null,
  };
  const params = new URLSearchParams(location.search);
  const main = $("#page-content");
  const csrf = () =>
    state.session.csrf_token || $('meta[name="csrf-token"]').content;
  function errorText(detail) {
    if (Array.isArray(detail))
      return detail
        .map((item) => `${(item.loc || []).slice(1).join(".")}: ${item.msg}`)
        .join("；");
    return typeof detail === "string"
      ? detail
      : "请求未完成，请查看服务日志后重试。";
  }
  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...options.headers };
    if (options.method && options.method !== "GET")
      headers["X-CSRF-Token"] = csrf();
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(options.body);
    }
    let response;
    try {
      response = await fetch(path, { ...options, headers });
    } catch (_) {
      throw new Error("连接服务失败，请确认本地服务或服务器仍在运行。");
    }
    if (response.status === 401) {
      location.assign("/login");
      throw new Error("登录已过期，请重新登录。");
    }
    let body;
    try {
      body = await response.json();
    } catch (_) {
      throw new Error(`服务器返回异常（${response.status}），请查看服务日志。`);
    }
    if (!response.ok) throw new Error(errorText(body.detail));
    return body;
  }
  let toastTimeout;
  function toast(message, isError = false) {
    const target = $("#toast");
    target.textContent = message;
    target.classList.toggle("is-error", isError);
    target.hidden = false;
    clearTimeout(toastTimeout);
    toastTimeout = setTimeout(
      () => {
        target.hidden = true;
      },
      isError ? 10000 : 5500,
    );
  }
  function showError(message) {
    const el = $("#global-error");
    el.textContent = message;
    el.hidden = false;
  }
  function clearError() {
    $("#global-error").hidden = true;
  }
  function dateTime(value) {
    if (!value) return "尚无记录";
    const date = new Date(value);
    return Number.isNaN(date.valueOf())
      ? escape(value)
      : escape(
          new Intl.DateTimeFormat("zh-CN", {
            timeZone: "Asia/Shanghai",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          }).format(date),
        );
  }
  const platform = (name) => ({meituan: "美团外卖", jd: "京东秒送", eleme: "饿了么 / 淘宝闪购"}[name] || name);
  function badge(text, type = "neutral") {
    return `<span class="badge ${type}">${escape(text)}</span>`;
  }
  const statusName = {
    pending: "待接单",
    accepted: "已接单",
    completed: "已完成",
    cancelled: "已取消",
    running: "同步中",
    success: "成功",
    error: "失败",
    busy: "正在同步",
  };
  function statusBadge(status) {
    return badge(
      statusName[status] || status,
      {
        completed: "success",
        success: "success",
        cancelled: "neutral",
        error: "error",
        running: "warning",
        accepted: "success",
        pending: "warning",
      }[status] || "neutral",
    );
  }
  const refundName = {
    none: "无已知退款",
    partial: "部分退款",
    full: "整单退款",
    unknown: "售后未对账",
  };
  const sourceName = {
    platform: "平台明细",
    rule: "门店规则",
    name_split: "名称推断",
    original: "原商品",
  };
  function empty(title, description, href = "", action = "") {
    return `<div class="empty-state"><div class="empty-icon">${icon("orders")}</div><h3>${escape(title)}</h3><p>${escape(description)}</p>${href ? `<a class="button secondary compact" href="${escape(href)}">${escape(action)}</a>` : ""}</div>`;
  }
  function warnings(data) {
    const messages = data.warnings || [];
    return messages.length
      ? `<div class="notice warning">${icon("info")}<ul>${messages.map((message) => `<li>${escape(message)}</li>`).join("")}</ul></div>`
      : "";
  }
  function filters() {
    const result = new URLSearchParams({
      start_date: $("#start-date").value,
      end_date: $("#end-date").value,
    });
    if ($("#merchant-filter").value)
      result.set("merchant_id", $("#merchant-filter").value);
    if ($("#platform-filter").value)
      result.set("platform", $("#platform-filter").value);
    return result;
  }
  function selectedMerchants() {
    const merchant = $("#merchant-filter").value,
      channel = $("#platform-filter").value;
    return state.merchants.filter(
      (item) =>
        (!merchant || String(item.id) === merchant) &&
        (!channel || item.platform === channel),
    );
  }
  function updateDemoNotice() {
    $("#demo-notice").hidden = !selectedMerchants().some(
      (item) => item.is_demo,
    );
  }
  function saveFilters() {
    const value = filters();
    history.replaceState(null, "", `${location.pathname}?${value}`);
    $$(".nav-link, [data-preserve-filters]").forEach((link) => {
      const url = new URL(link.href);
      url.search = value.toString();
      link.href = url.pathname + url.search;
    });
  }
  function ensureDates() {
    if (!$("#start-date").value || !$("#end-date").value)
      throw new Error("请选择开始和结束日期。");
    if ($("#start-date").value > $("#end-date").value)
      throw new Error("结束日期不能早于开始日期。");
  }
  function quantityText(totals, review = false) {
    if (!totals || !totals.length) return "0";
    return totals
      .map(
        (item) =>
          `<span>${escape(review ? item.review_quantity : item.quantity)}<span class="metric-unit">${escape(item.unit)}</span></span>`,
      )
      .join("");
  }
  function summaryCards(data) {
    const s = data.summary,
      totals = s.quantity_by_unit || [],
      multiple = totals.length > 1;
    return `<div class="metrics"><article class="metric"><div class="metric-label">${icon("orders")}全部订单</div><div class="metric-value">${escape(s.order_count)}<span class="metric-unit">笔</span></div><p class="metric-note">有效 ${escape(s.active_order_count)} 笔 / 取消 ${escape(s.cancelled_count)} 笔</p></article><article class="metric"><div class="metric-label">${icon("sales")}商品计入销量</div><div class="metric-value ${multiple ? "multi-unit" : ""}">${quantityText(totals)}</div><p class="metric-note">按拆分后的商品统计，单位分开计</p></article><article class="metric"><div class="metric-label">${icon("money")}订单金额（扣已知退款）</div><div class="metric-value currency"><span class="metric-unit">¥</span> ${escape(s.revenue)}</div><p class="metric-note">用户实付口径，非平台结算收入</p></article><article class="metric review"><div class="metric-label">${icon("split")}待确认的拆分销量</div><div class="metric-value ${multiple ? "multi-unit" : ""}">${quantityText(totals, true)}</div><p class="metric-note">名称推断需核实，再用于备货分析</p></article></div>`;
  }
  function trendChart(rows) {
    if (!rows.length)
      return empty(
        "还没有销量趋势",
        "同步订单后，这里会按营业日显示商品销量。",
      );
    const units = [...new Set(rows.map((row) => row.unit))];
    return units
      .map((unit) => {
        const items = rows.filter((row) => row.unit === unit),
          max = Math.max(1, ...items.map((row) => Number(row.quantity)));
        return `<figure class="trend-group"><div class="chart-unit">单位：${escape(unit)}</div><div class="chart-scroll"><div class="bar-chart ${items.length > 10 ? "long-chart" : ""}" role="list" aria-label="${escape(unit)}销量趋势">${items.map((row) => `<div class="bar-column" role="listitem" aria-label="${escape(row.date)}：${escape(row.quantity)} ${escape(unit)}"><span class="bar-number">${escape(row.quantity)}</span><span class="bar-fill" style="height:${(Math.max(0, Number(row.quantity)) / max) * 75}%"></span><span class="bar-date">${escape(row.date.slice(5).replace("-", "/"))}</span></div>`).join("")}</div></div></figure>`;
      })
      .join("");
  }
  function rankings(products) {
    if (!products.length)
      return empty("还没有商品排名", "拉取门店订单后，商品销量会在这里汇总。");
    return [...new Set(products.map((row) => row.unit))]
      .map((unit) => {
        const items = products
            .filter((row) => row.unit === unit)
            .sort((a, b) => Number(b.sold_quantity) - Number(a.sold_quantity))
            .slice(0, 5),
          max = Math.max(1, ...items.map((row) => Number(row.sold_quantity)));
        return `<div class="rank-group"><div class="chart-unit">${escape(unit)} / 按同单位排序</div>${items.map((row, index) => `<div class="rank-row"><span class="rank-position">${index + 1}</span><div class="rank-info"><div class="rank-name">${escape(row.name)} ${Number(row.review_quantity) ? badge("待确认", "warning") : ""}</div><div class="rank-track"><span class="rank-fill" style="width:${(Math.max(0, Number(row.sold_quantity)) / max) * 100}%"></span></div></div><span class="rank-value number">${escape(row.sold_quantity)} <small class="muted">${escape(unit)}</small></span></div>`).join("")}</div>`;
      })
      .join("");
  }
  function freshness(runs) {
    const merchants = selectedMerchants();
    if (!merchants.length) return "";
    return `<section class="panel"><div class="panel-header"><div><h2>数据同步情况</h2><p>各门店最近一次同步，不代表当前日期区间已完整覆盖</p></div><a class="text-button" href="/syncs?${filters()}">查看记录</a></div><div class="freshness-list">${merchants
      .map((merchant) => {
        const latest = runs.find((run) => run.merchant_id === merchant.id);
        return `<div class="freshness-item"><div class="freshness-title"><span>${escape(merchant.name)}</span>${merchant.is_demo ? badge("演示", "warning") : latest ? statusBadge(latest.status) : badge("尚未同步")}</div><div class="freshness-meta">${merchant.is_demo ? "由 demo 命令显式导入，不连接平台" : latest ? `营业日 ${escape(latest.target_date)} · ${dateTime(latest.finished_at || latest.started_at)} · 取回 ${escape(latest.fetched)} 笔` : "请先配置门店授权，然后手动同步或启动定时进程"}</div>${latest?.status === "error" ? `<p class="freshness-error">${escape(latest.error)}</p>` : ""}</div>`;
      })
      .join("")}</div></section>`;
  }
  async function dashboard(loadToken, quiet = false) {
    const query = filters();
    const [data, sync] = await Promise.all([
      api(`/api/sales?${query}`),
      api("/api/sync-runs?limit=100"),
    ]);
    if (loadToken !== loadId) return;
    main.innerHTML = `${warnings(data)}${summaryCards(data)}${!data.summary.order_count ? `<section class="panel">${empty("这个日期范围还没有订单", state.merchants.length ? "请检查门店、日期和同步记录。尚未接入的门店需要先配置授权。" : "先添加一个门店，再配置对应平台的授权，开始汇总每天的订单。", "/merchants", "前往门店接入")}</section>` : ""}<div class="dashboard-grid"><section class="panel"><div class="panel-header"><div><h2>销量趋势</h2><p>${escape(data.start_date)} 至 ${escape(data.end_date)} / 拆分商品口径</p></div>${badge("按营业日")}</div>${trendChart(data.trend)}</section><section class="panel"><div class="panel-header"><div><h2>热销商品</h2><p>每个单位分别展示前 5 名</p></div><a class="text-button" href="/sales?${query}">全部商品</a></div>${rankings(data.products)}</section></div>${freshness(sync.items)}<div class="methodology">${escape(data.methodology)}<br>采购建议还需要配方、库存和供应周期，可通过 MCP 交给 dsh 继续分析。</div>`;
  }
  function salesTable(products) {
    if (!products.length)
      return empty(
        "没有符合条件的商品",
        state.confirmed
          ? "当前仅统计已确认拆分。可取消勾选，或先保存拆分规则并重算历史。"
          : "请调整日期或查看同步记录，确认门店订单已写入。",
      );
    return `<div class="table-wrap"><table><thead><tr><th>商品名称</th><th>单位</th><th class="align-right">下单数量</th><th class="align-right">计入销量</th><th class="align-right">已知退货</th><th class="align-right">待确认</th><th class="align-right">关联订单</th></tr></thead><tbody>${products.map((row) => `<tr><td><span class="primary-text">${escape(row.name)}</span>${Number(row.review_quantity) ? `<div class="secondary-text">名称推断明细，建议核实拆分规则</div>` : ""}</td><td>${escape(row.unit)}</td><td class="align-right number">${escape(row.ordered_quantity)}</td><td class="align-right number">${escape(row.sold_quantity)}</td><td class="align-right">${escape(row.refunded_quantity)}</td><td class="align-right">${Number(row.review_quantity) ? badge(row.review_quantity, "warning") : '<span class="muted">0</span>'}</td><td class="align-right">${escape(row.order_count)} 笔</td></tr>`).join("")}</tbody></table></div>`;
  }
  async function sales(loadToken, quiet = false) {
    const query = filters();
    query.set("confirmed_only", state.confirmed);
    const data = await api(`/api/sales?${query}`);
    if (loadToken !== loadId) return;
    main.innerHTML = `${warnings(data)}${summaryCards(data)}<section class="panel"><div class="panel-header"><div><h2>商品销售明细</h2><p>共 ${escape(data.summary.product_count)} 个商品与单位组合</p></div><a class="button secondary compact" href="/api/export/sales.csv?${query}">${icon("download")}导出 CSV</a></div><div class="table-toolbar"><span class="muted" style="font-size:11px">相同名称、不同单位分别统计</span><label class="checkbox-label"><input id="confirmed-only" type="checkbox" ${state.confirmed ? "checked" : ""}>仅看已确认拆分</label></div>${salesTable(data.products)}<div class="panel-footer">京东数量为原始下单口径，部分退款和售后尚未对账，不能当作最终净销量。</div></section><div class="methodology">${escape(data.methodology)}</div>`;
    $("#confirmed-only").addEventListener("change", (event) => {
      state.confirmed = event.target.checked;
      loadPage();
    });
  }
  function orderDetails(order) {
    return `<div class="order-detail"><div class="detail-meta"><span>原始订单号 <strong class="mono">${escape(order.external_id)}</strong></span><span>归属营业日 ${escape(order.business_date)}</span><span>最近入库（北京时间）${dateTime(order.synced_at)}</span></div>${order.lines.map((line) => `<div class="original-line"><div><div class="original-name">${escape(line.name)}</div><div class="original-quantity">原商品 ${escape(line.quantity)} ${escape(line.unit)}${Number(line.refunded_quantity) ? ` / 已知退货 ${escape(line.refunded_quantity)}` : ""}</div><div class="secondary-text">商品行 ${escape(line.external_id)}</div></div><div class="component-list">${line.components.map((component) => `<div class="component-line"><div><span class="primary-text">${escape(component.name)}</span> ${component.needs_review ? badge("待确认", "warning") : ""}<div class="secondary-text">${escape(sourceName[component.source] || component.source)}${Number(component.refunded_quantity) ? ` / 退货 ${escape(component.refunded_quantity)} ${escape(component.unit)}` : ""}</div></div><span class="component-amount number">${escape(component.quantity)} <small class="muted">${escape(component.unit)}</small></span></div>`).join("")}</div></div>`).join("")}<p class="secondary-text">所有拆分子项共用上方原订单号，金额仅记在原订单中。</p></div>`;
  }
  async function orders(loadToken, quiet = false) {
    const query = filters();
    query.set("page", state.page);
    query.set("page_size", "20");
    if (state.orderStatus) query.set("status", state.orderStatus);
    if (state.query) query.set("query", state.query);
    const data = await api(`/api/orders?${query}`),
      pages = Math.max(1, Math.ceil(data.total / data.page_size));
    if (loadToken !== loadId) return;
    main.innerHTML = `${warnings(data)}<section class="panel"><div class="table-toolbar"><form id="order-search" class="search-field">${icon("search")}<input name="query" aria-label="搜索订单号、流水号或商品" placeholder="搜索订单号、流水号或商品" value="${escape(state.query)}"></form><select id="order-status" aria-label="订单状态"><option value="">全部订单状态</option>${Object.entries(
      statusName,
    )
      .filter(([key]) =>
        ["pending", "accepted", "completed", "cancelled"].includes(key),
      )
      .map(
        ([key, value]) =>
          `<option value="${key}" ${state.orderStatus === key ? "selected" : ""}>${value}</option>`,
      )
      .join(
        "",
      )}</select><button class="button secondary compact" data-action="search-orders">查询</button><span class="muted" style="font-size:11px;margin-left:auto">共 ${escape(data.total)} 笔订单</span></div>${data.items.length ? `<div class="table-wrap"><table><thead><tr><th>订单 / 流水号</th><th>门店 / 平台</th><th>下单时间（北京时间）</th><th>商品</th><th>订单状态</th><th class="align-right">实付金额</th></tr></thead><tbody>${data.items.map((order) => `<tr><td><button class="order-head" data-action="toggle-order" data-id="${order.id}" aria-expanded="false" aria-controls="order-detail-${order.id}">${icon("chevron")}<span><span class="primary-text">${escape(order.serial_no || "查看订单")}</span><span class="secondary-text mono" style="display:block">${escape(order.external_id)}</span></span></button></td><td><span>${escape(order.merchant_name)}</span><div class="inline-tags">${badge(platform(order.platform), order.platform)}${state.merchants.find((item) => item.id === order.merchant_id)?.is_demo ? badge("演示", "warning") : ""}</div></td><td class="nowrap">${dateTime(order.placed_at)}</td><td><span class="primary-text">${escape(order.lines[0]?.name || "无商品行")}</span><div class="secondary-text">${escape(order.lines.length)} 个原商品行 / ${escape(order.lines.reduce((sum, line) => sum + line.components.length, 0))} 个明细</div></td><td>${statusBadge(order.status)}<div class="secondary-text">${escape(refundName[order.refund_status] || order.refund_status)}</div></td><td class="align-right number nowrap">¥ ${escape(order.total_amount)}${Number(order.refund_amount) ? `<div class="secondary-text">已知退款 ¥${escape(order.refund_amount)}</div>` : ""}</td></tr><tr class="order-detail-row" id="order-detail-${order.id}" hidden><td colspan="6">${orderDetails(order)}</td></tr>`).join("")}</tbody></table></div>` : empty("没有找到订单", "检查当前门店、日期、状态和搜索内容，也可以查看同步记录是否成功。")}<div class="pagination"><span>第 ${escape(data.page)} / ${pages} 页，每页 ${escape(data.page_size)} 笔</span><div class="button-row"><button class="button secondary compact" data-action="previous-page" ${state.page <= 1 ? "disabled" : ""}>上一页</button><button class="button secondary compact" data-action="next-page" ${state.page >= pages ? "disabled" : ""}>下一页</button></div></div></section><div class="methodology">点击流水号展开原商品与拆分明细；父商品购买数量已经乘入子项。取消和退款状态保留在原订单中。京东原始下单口径的售后数据尚未对账。</div>`;
    $("#order-search").addEventListener("submit", (event) => {
      event.preventDefault();
      searchOrders();
    });
    $("#order-status").addEventListener("change", searchOrders);
  }
  function searchOrders() {
    state.query = $("#order-search input").value.trim();
    state.orderStatus = $("#order-status").value;
    state.page = 1;
    loadPage();
  }
  function merchantOptions(value = "", realOnly = false) {
    return state.merchants
      .filter((item) => !realOnly || !item.is_demo)
      .map(
        (item) =>
          `<option value="${item.id}" ${String(value) === String(item.id) ? "selected" : ""}>${escape(item.name)}${item.is_demo ? "（演示）" : ""}</option>`,
      )
      .join("");
  }
  async function rules(loadToken, quiet = false) {
    const query = new URLSearchParams();
    if ($("#merchant-filter").value)
      query.set("merchant_id", $("#merchant-filter").value);
    const data = await api(`/api/rules?${query}`);
    if (loadToken !== loadId) return;
    state.rules = data.items.filter((rule) =>
      selectedMerchants().some((merchant) => merchant.id === rule.merchant_id),
    );
    main.innerHTML = `<div class="two-column"><section class="panel"><div class="panel-header"><div><h2>门店拆分规则</h2><p>按完整商品名精确匹配，不影响其他门店</p></div><button class="button compact" data-action="new-rule">${icon("plus")}新增规则</button></div>${state.rules.length ? `<div class="table-wrap"><table><thead><tr><th>原始商品 / 门店</th><th>拆分商品与倍数</th><th>状态</th><th>操作</th></tr></thead><tbody>${state.rules.map((rule) => `<tr><td><span class="primary-text">${escape(rule.match_name)}</span><div class="secondary-text">${escape(state.merchants.find((item) => item.id === rule.merchant_id)?.name || rule.merchant_id)}</div></td><td>${rule.components.map((part) => `<span class="rule-component">${escape(part.name)} <strong>× ${escape(part.factor)} ${escape(part.unit)}</strong></span>`).join("")}</td><td>${badge(rule.enabled ? "已启用" : "已停用", rule.enabled ? "success" : "neutral")}</td><td><button class="text-button" data-action="edit-rule" data-id="${rule.id}">编辑</button></td></tr>`).join("")}</tbody></table></div>` : empty("把套餐记成明白账", "例如“半只鸡+半只鸭”，可配置成两个子商品，每份套餐各记 1 份。保存规则后可重算该店历史订单。")}<div class="panel-footer">平台结构化套餐优先，其次是门店精确规则；名称加号推断会标为待确认。</div></section><aside><section class="panel"><div class="panel-header"><h2>拆分与备货的口径</h2></div><div class="panel-body" style="padding-top:0"><ul class="help-list"><li><strong>倍数是一份父商品里的数量</strong><p>套餐买 2 份，子商品倍数为 1，就记录该子商品 2 份；原订单号和商品行保持关联。</p></li><li><strong>“半只”先保留为商品名称</strong><p>系统不会擅自将其换算为 0.5 只或公斤。实际耗料需要结合配方和出成率。</p></li><li><strong>规则修改后可重算历史</strong><p>重算只调整该门店已存订单的拆分明细；不会修改原始商品数量，也不会重新拉平台。</p></li></ul></div></section><section class="panel"><div class="panel-header"><h2>按最新规则重算历史</h2></div><div class="panel-body" style="padding-top:0"><div class="field"><label for="reprocess-merchant">需要重算的门店</label><select id="reprocess-merchant"><option value="">选择门店</option>${merchantOptions($("#merchant-filter").value)}</select><small>将重建所选门店全部历史订单的拆分明细。原始订单保留。</small></div><button class="button secondary full-width" data-action="reprocess">重算该店历史明细</button></div></section></aside></div>`;
  }
  function field(label, name, value = "", extra = "") {
    return `<div class="field"><label for="${name}">${label}</label><input id="${name}" name="${name}" value="${escape(value)}" ${extra}></div>`;
  }
  function openDialog(title, kicker, html) {
    $("#editor-title").textContent = title;
    $("#editor-kicker").textContent = kicker;
    $("#editor-body").innerHTML = html;
    icons($("#editor"));
    $("#editor").showModal();
  }
  function formError(form, message) {
    let target = $(".form-error", form);
    if (!target) {
      target = document.createElement("div");
      target.className = "form-error";
      target.setAttribute("role", "alert");
      form.prepend(target);
    }
    target.textContent = message;
    target.scrollIntoView({ block: "nearest" });
  }
  function rulePart(part = {}) {
    return `<div class="split-row"><div class="field"><label>子商品名称<input data-part="name" value="${escape(part.name || "")}" maxlength="500" required placeholder="例如：半只鸡"></label></div><div class="field"><label>每份倍数<input data-part="factor" type="number" min="0.0001" max="999999" step="0.0001" value="${escape(part.factor || "1")}" required></label></div><div class="field"><label>单位<input data-part="unit" value="${escape(part.unit || "份")}" maxlength="30" required></label></div><button type="button" class="icon-button" data-action="remove-part" aria-label="删除该子商品">${icon("close")}</button></div>`;
  }
  function ruleParts(form) {
    return $$(".split-row", form).map((row) => ({
      name: $('[data-part="name"]', row).value.trim(),
      factor: $('[data-part="factor"]', row).value,
      unit: $('[data-part="unit"]', row).value.trim(),
    }));
  }
  function editRule(id) {
    if (!state.merchants.length) {
      toast("请先在门店接入中添加门店。", true);
      return;
    }
    const rule = state.rules.find((item) => item.id === Number(id));
    openDialog(
      rule ? "编辑拆分规则" : "新增拆分规则",
      "套餐拆分",
      `<form id="rule-form"><div class="field"><label for="rule-merchant">所属门店</label><select name="merchant_id" id="rule-merchant" required><option value="">选择门店</option>${merchantOptions(rule?.merchant_id || $("#merchant-filter").value)}</select></div>${field("平台上的完整商品名", "match_name", rule?.match_name || "", 'required maxlength="500" placeholder="例如：半只鸡+半只鸭"')}<div class="form-section"><h3>一份父商品包含</h3><div id="rule-parts">${(
        rule?.components || [
          { name: "", factor: "1", unit: "份" },
          { name: "", factor: "1", unit: "份" },
        ]
      )
        .map(rulePart)
        .join(
          "",
        )}</div><button class="text-button" type="button" data-action="add-part">＋ 添加子商品</button></div><div class="field-grid" style="margin-top:15px">${field("预览购买份数", "preview_quantity", "2", 'type="number" min="0.0001" step="0.0001" required')}<div class="field" style="justify-content:flex-end"><button class="button secondary" type="button" data-action="preview-rule">预览拆分数量</button></div></div><div id="rule-preview" hidden></div><label class="checkbox-label"><input type="checkbox" name="enabled" ${!rule || rule.enabled ? "checked" : ""}>启用这条规则</label><p class="field-hint">保存后影响后续同步。已有订单需另点“重算该店历史明细”。平台结构化套餐仍优先于规则。</p><div class="form-actions"><button class="button secondary" type="button" data-action="close-dialog">取消</button><button class="button" type="submit">保存规则</button></div></form>`,
    );
    const ruleForm = $("#rule-form");
    const invalidatePreview = () => {
      const target = $("#rule-preview", ruleForm);
      target.hidden = true;
      target.replaceChildren();
    };
    ruleForm.addEventListener("input", invalidatePreview);
    ruleForm.addEventListener("change", invalidatePreview);
    ruleForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget,
        button = $("[type=submit]", form);
      button.disabled = true;
      try {
        const components = ruleParts(form);
        if (!components.length) throw new Error("请至少添加一个子商品。");
        const payload = {
          merchant_id: Number(form.elements.merchant_id.value),
          match_name: form.elements.match_name.value.trim(),
          enabled: form.elements.enabled.checked,
          components,
        };
        await api(rule ? `/api/rules/${rule.id}` : "/api/rules", {
          method: rule ? "PUT" : "POST",
          body: payload,
        });
        $("#editor").close();
        toast("拆分规则已保存。已有订单可按新规则重算。");
        await loadPage();
      } catch (error) {
        formError(form, error.message);
      } finally {
        button.disabled = false;
      }
    });
  }
  async function previewRule() {
    const form = $("#rule-form");
    if (!form.reportValidity()) return;
    const editor = $("#editor");
    const target = $("#rule-preview", form);
    const readPayload = () => ({
      name: form.elements.match_name.value,
      quantity: form.elements.preview_quantity.value,
      components: ruleParts(form),
    });
    const payload = readPayload();
    const snapshot = JSON.stringify(payload);
    const isCurrent = () =>
      form.isConnected &&
      $("#rule-form") === form &&
      editor.open &&
      editor.contains(form);
    target.hidden = true;
    target.replaceChildren();
    try {
      const data = await api("/api/rules/preview", {
        method: "POST",
        body: payload,
      });
      if (!isCurrent()) return;
      if (JSON.stringify(readPayload()) !== snapshot) {
        target.hidden = false;
        target.className = "preview-box";
        target.textContent = "输入已更改，请重新预览拆分数量。";
        return;
      }
      target.hidden = false;
      target.className = "preview-box";
      target.innerHTML = `<strong>购买 ${escape(payload.quantity)} 份的拆分结果</strong>${data.items.map((item) => `<div class="preview-item"><span>${escape(item.name)}</span><span class="number">${escape(item.quantity)} ${escape(item.unit)}</span></div>`).join("")}<p class="field-hint">这些子项仍属于同一笔订单。</p>`;
    } catch (error) {
      if (isCurrent() && JSON.stringify(readPayload()) === snapshot)
        formError(form, error.message);
    }
  }
  async function merchants(loadToken, quiet = false) {
    main.innerHTML = `<div class="notice">${icon("info")}<span><strong>官方 API 接入暂不可用，相关配置仅保留。</strong>商家后台账号不能直接用于此处授权。这里只填写环境变量的名称。AppKey、AppSecret、AccessToken 的真实值保存在服务器或本机 <code>.env</code> 中，修改后重启服务。</span></div><section class="panel"><div class="panel-header"><div><h2>已添加门店</h2><p>${state.merchants.length} 家门店 / 平台授权分别管理</p></div><button class="button compact" data-action="new-merchant">${icon("plus")}添加门店</button></div>${state.merchants.length ? state.merchants.map((merchant) => `<article class="merchant-card"><div class="merchant-card-heading"><div><div class="merchant-name"><h3>${escape(merchant.name)}</h3>${badge(platform(merchant.platform), merchant.platform)}${merchant.is_demo ? badge("演示门店", "warning") : badge("旧API暂不可用", "neutral")}</div><p class="secondary-text">门店编号 <span class="mono">${escape(merchant.external_shop_id)}</span></p></div><div class="button-row"><button class="button secondary compact" data-action="edit-merchant" data-id="${merchant.id}">${icon("edit")}编辑</button>${merchant.is_demo ? "" : `<button class="button secondary compact" data-action="sync-current" data-id="${merchant.id}">${icon("sync")}采集任务</button>`}</div></div><dl class="merchant-meta"><div><dt>凭据状态</dt><dd>${merchant.is_demo ? badge("演示不需要凭据", "warning") : badge(merchant.configured ? "凭据已配置" : "凭据未配齐", merchant.configured ? "success" : "warning")}</dd></div><div><dt>同步间隔</dt><dd>每 ${escape(merchant.sync_interval_minutes)} 分钟</dd></div><div><dt>门店时区</dt><dd>${escape(merchant.timezone)}</dd></div></dl><p class="connection-note ${!merchant.configured && !merchant.is_demo ? "missing" : ""}">${escape(merchant.connector_status)}${!merchant.configured && !merchant.is_demo ? `<br>缺少：${escape(merchant.missing_configuration.join("、"))}` : ""}</p></article>`).join("") : empty("添加第一家门店", "选择美团、饿了么 / 淘宝闪购或京东秒送，填写真实门店编号；再到Agent定时任务配置采集。")}<div class="panel-footer">凭据存在只表示配置齐全，实际调用权限和数据合同仍需真实门店联调。京东响应结构与部分退款接口有待核对。</div></section><section class="panel"><div class="panel-header"><h2>通过Agent采集</h2></div><div class="panel-body guide-prose" style="padding-top:0"><p>在CDP浏览器登录商家后台，然后到“Agent定时任务”选择本地agy/dsh、设置提示词和间隔，由Agent自行采集、调用数据库技能写入并核对。</p><p>自动执行需要同一工程运行 <code>uv run takeaway worker</code>。本页旧API环境变量与同步开关仅保留，暂不可用，不控制Agent调度。</p></div></section>`;
  }
  function editMerchant(id) {
    const merchant = state.merchants.find((item) => item.id === Number(id)),
      c = merchant?.configuration || {};
    openDialog(
      merchant ? "编辑门店" : "添加门店",
      "门店接入",
      `<form id="merchant-form">${merchant?.is_demo ? '<div class="notice warning">这是演示门店，只用于体验；不能启用平台同步或改成真实门店。</div>' : ""}${field("门店名称", "name", merchant?.name || "", 'required maxlength="120" placeholder="例如：巷口烧味 · 南门店"')}<div class="field-grid"><div class="field"><label for="merchant-platform">平台</label><select id="merchant-platform" name="platform"><option value="meituan" ${merchant?.platform === "meituan" ? "selected" : ""}>美团外卖</option><option value="eleme" ${!merchant || merchant.platform === "eleme" ? "selected" : ""}>饿了么 / 淘宝闪购</option><option value="jd" ${merchant?.platform === "jd" ? "selected" : ""}>京东秒送</option></select></div>${field("平台门店编号", "external_shop_id", merchant?.external_shop_id || "", 'required maxlength="120"')}</div><div class="field-grid">${field("门店时区", "timezone", merchant?.timezone || "Asia/Shanghai", "required")}${field("同步间隔（分钟）", "sync_interval_minutes", merchant?.sync_interval_minutes || "15", 'required type="number" min="1" max="1440" step="1"')}</div><p class="field-hint" style="margin-bottom:18px">已有历史订单后，平台、门店编号和时区不可改绑；请另加新门店。</p><div class="form-section"><h3>旧API配置（暂不可用）</h3><p>输入变量名，不输入真实密钥。每家门店单独配置。</p>${field("AppKey 变量名", "app_key_env", c.app_key_env || "", 'pattern="[A-Z][A-Z0-9_]*" placeholder="ELM_SHOP1_APP_KEY"')}${field("AppSecret 变量名", "app_secret_env", c.app_secret_env || "", 'pattern="[A-Z][A-Z0-9_]*" placeholder="ELM_SHOP1_APP_SECRET"')}${field("AccessToken 变量名", "access_token_env", c.access_token_env || "", 'pattern="[A-Z][A-Z0-9_]*" placeholder="ELM_SHOP1_ACCESS_TOKEN"')}</div><div class="field-grid"><div class="field"><label for="environment">接口环境</label><select id="environment" name="environment"><option value="production" ${c.environment !== "sandbox" ? "selected" : ""}>正式环境</option><option value="sandbox" ${c.environment === "sandbox" ? "selected" : ""}>饿了么沙箱（京东不支持）</option></select></div><div class="field"><label for="bundle-mode">平台套餐子数量口径</label><select id="bundle-mode" name="bundle_quantity_mode"><option value="">尚未确认</option><option value="per_parent" ${c.bundle_quantity_mode === "per_parent" ? "selected" : ""}>每份父商品内的子数量</option><option value="order_total" ${c.bundle_quantity_mode === "order_total" ? "selected" : ""}>整行父商品的子数量合计</option></select></div></div><div class="notice warning">京东使用新版接口。公开返回文档存在矛盾，需实店联调；京东部分退款和售后未对账，数量为原始下单口径。</div><label class="checkbox-label"><input name="enabled" type="checkbox" ${merchant?.enabled ? "checked" : ""} ${merchant?.is_demo ? "disabled" : ""}>旧API定时配置（暂不可用，不影响Agent任务）</label><div class="form-actions"><button class="button secondary" type="button" data-action="close-dialog">取消</button><button class="button" type="submit">保存门店</button></div></form>`,
    );
    $("#merchant-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget,
        button = $("[type=submit]", form);
      button.disabled = true;
      try {
        const configuration = { ...c };
        for (const key of [
          "app_key_env",
          "app_secret_env",
          "access_token_env",
          "environment",
          "bundle_quantity_mode",
        ]) {
          const value = form.elements[key].value.trim();
          if (value) configuration[key] = value;
          else delete configuration[key];
        }
        if (form.elements.platform.value === "jd")
          configuration.api_mode = "new";
        else delete configuration.api_mode;
        const payload = {
          name: form.elements.name.value.trim(),
          platform: form.elements.platform.value,
          external_shop_id: form.elements.external_shop_id.value.trim(),
          timezone: form.elements.timezone.value.trim(),
          sync_interval_minutes: Number(
            form.elements.sync_interval_minutes.value,
          ),
          enabled: form.elements.enabled.checked,
          configuration,
        };
        await api(
          merchant ? `/api/merchants/${merchant.id}` : "/api/merchants",
          { method: merchant ? "PUT" : "POST", body: payload },
        );
        $("#editor").close();
        toast("门店配置已保存。真实凭据更改后请重启 web 和 worker。");
        await refreshMerchants();
        await loadPage();
      } catch (error) {
        formError(form, error.message);
      } finally {
        button.disabled = false;
      }
    });
  }
  async function syncs(loadToken, quiet = false) {
    const query = new URLSearchParams({ limit: "100" });
    if ($("#merchant-filter").value)
      query.set("merchant_id", $("#merchant-filter").value);
    const data = await api(`/api/sync-runs?${query}`),
      visible = data.items.filter((run) =>
        selectedMerchants().some((merchant) => merchant.id === run.merchant_id),
      );
    if (
      loadToken !== loadId ||
      (quiet && $("#sync-form")?.contains(document.activeElement))
    )
      return;
    const previousForm = quiet ? $("#sync-form") : null;
    main.innerHTML = `<div class="notice warning"><span>官方API同步暂不可用，以下表单仅保留。请到<a href="/agents">Agent定时任务</a>进行采集。</span></div><section class="panel"><div class="panel-header"><div><h2>旧API同步（暂不可用）</h2><p>每次最多 31 天，逐日完成后入库</p></div>${badge("只读平台订单")}</div><div class="panel-body" style="padding-top:0"><form id="sync-form" class="sync-form"><div class="field"><label for="sync-merchant">真实门店</label><select id="sync-merchant" name="merchant_id" required><option value="">选择门店</option>${merchantOptions($("#merchant-filter").value, true)}</select></div><div class="field"><label for="sync-start">开始营业日</label><input id="sync-start" name="start_date" type="date" value="${escape($("#start-date").value)}" required></div><div class="field"><label for="sync-end">结束营业日</label><input id="sync-end" name="end_date" type="date" value="${escape($("#end-date").value)}" required></div><button class="button" type="submit">${icon("sync")}开始同步</button></form><p class="field-hint">抓取完整分页后再写入。同店订单号重复拉取会更新；演示门店不连接平台。京东首次联调若响应结构不符会明确失败。</p></div></section><section class="panel"><div class="panel-header"><div><h2>最近同步记录</h2><p>最多显示最近 100 次；每 8 秒刷新</p></div><button class="button secondary compact" data-action="refresh">${icon("sync")}刷新</button></div>${visible.length ? `<div class="table-wrap"><table><thead><tr><th>门店</th><th>目标营业日</th><th>状态</th><th>开始 / 完成</th><th class="align-right">取回</th><th class="align-right">新增 / 更新</th><th>结果说明</th></tr></thead><tbody>${visible.map((run) => `<tr><td><span class="primary-text">${escape(run.merchant_name)}</span><div class="secondary-text">${escape(platform(run.platform))} / #${escape(run.id)}</div></td><td class="nowrap">${escape(run.target_date)}</td><td>${statusBadge(run.status)}</td><td class="nowrap">${dateTime(run.started_at)}<div class="secondary-text">${run.finished_at ? dateTime(run.finished_at) : "等待完成"}</div></td><td class="align-right number">${escape(run.fetched)}</td><td class="align-right">${escape(run.inserted)} / ${escape(run.updated)}</td><td class="${run.error ? "sync-error-cell" : "muted"}">${run.error ? escape(run.error) : run.status === "running" ? "正在获取或处理订单" : "当次任务已完成"}</td></tr>`).join("")}</tbody></table></div>` : empty("暂时没有同步记录", "旧API记录保留在此。浏览器采集的运行状态请查看Agent定时任务。")}<div class="panel-footer">某一天成功不代表所有历史已完整对账。此前日期发生的售后，需要再补拉相应营业日。</div></section>`;
    if (previousForm) $("#sync-form").replaceWith(previousForm);
    $("#sync-form").querySelectorAll("input,select,button").forEach(control => { control.disabled = true; });
    if (!previousForm)
      $("#sync-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = event.currentTarget,
          button = $("[type=submit]", form);
        button.disabled = true;
        try {
          await startSync(
            Number(form.elements.merchant_id.value),
            form.elements.start_date.value,
            form.elements.end_date.value,
          );
        } catch (error) {
          formError(form, error.message);
        } finally {
          button.disabled = false;
        }
      });
  }
  async function startSync(merchantId, start, end) {
    if (!merchantId) throw new Error("请选择真实门店。");
    const days = (Date.parse(end) - Date.parse(start)) / 86400000;
    if (!Number.isFinite(days) || days < 0 || days > 30)
      throw new Error("请选择正向日期范围，每次最多 31 天。");
    await api("/api/sync", {
      method: "POST",
      body: { merchant_id: merchantId, start_date: start, end_date: end },
    });
    toast("同步任务已提交，请查看记录中的完成状态。");
    if (page === "syncs") await loadPage();
  }
  const stdioConfig = `- id: mcp-takeaway
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: takeaway
    transport: stdio
    command: uv
    args:
      - --directory
      - /你的绝对路径/takeaway-insight
      - run
      - takeaway-mcp
    cwd: /你的绝对路径/takeaway-insight`;
  const httpConfig =
    "- id: mcp-takeaway\n  name: '@deepseek-ai/dsh-mcp-client'\n  config:\n    serverName: takeaway\n    transport: streamable-http\n    url: https://你的域名/mcp\n    headers:\n      Authorization: !!js '`Bearer ${process.env.TAKEAWAY_MCP_TOKEN}`'";
  async function mcpGuide(loadToken, quiet = false) {
    const tools = [
      ["list_merchants", "查看门店 ID、平台和同步配置状态。"],
      ["get_sync_status", "先检查最近同步结果和数据新鲜度。"],
      ["get_sales_summary", "查询日期范围、门店或平台的商品销量。"],
      ["get_orders", "分页查看订单、原商品行与拆分明细。"],
      ["get_product_trend", "查看指定商品的逐日销量，区分单位。"],
    ];
    main.innerHTML = `<div class="notice">${icon("mcp")}<span>MCP 提供 5 个只读工具，直接查询本系统的数据。它不会替代平台授权，也不会让 dsh 直接接单或修改订单。</span></div><section class="panel"><div class="panel-header"><div><h2>可用的分析工具</h2><p>网页与 MCP 使用相同的查询和统计口径</p></div>${badge(state.session.mcp_http_enabled ? "HTTP token 已配置" : "HTTP token 未配置", state.session.mcp_http_enabled ? "success" : "warning")}</div><div class="panel-body tools-grid" style="padding-top:0">${tools.map(([name, description]) => `<article class="tool-item"><code>${name}</code><p>${description}</p></article>`).join("")}</div></section><div class="guide-columns"><section class="panel"><div class="panel-header copy-row"><div><h2>本地 stdio</h2><p>配置文件：config/dsh.stdio.yml</p></div><button class="button secondary compact" data-action="copy-config" data-kind="stdio">${icon("copy")}复制</button></div><div class="panel-body guide-prose" style="padding-top:0"><p>在 dsh 的插件 YAML 中加入此配置，把两处路径改为工程的绝对路径。dsh 启动 MCP 子进程，读取该目录的私有环境配置。</p><pre><code>${escape(stdioConfig)}</code></pre></div></section><section class="panel"><div class="panel-header copy-row"><div><h2>服务器 HTTP</h2><p>配置文件：config/dsh.http.yml</p></div><button class="button secondary compact" data-action="copy-config" data-kind="http">${icon("copy")}复制</button></div><div class="panel-body guide-prose" style="padding-top:0"><p>使用部署后的 HTTPS 域名。客户端环境中的 <code>TAKEAWAY_MCP_TOKEN</code> 应与服务器独立 token 相同；它与网页密码分开。</p><pre><code>${escape(httpConfig)}</code></pre></div></section></div><section class="panel" style="margin-top:20px"><div class="panel-header"><h2>交给 dsh 的分析问题</h2></div><div class="panel-body guide-prose" style="padding-top:0"><p>“先检查门店和同步状态，统计今天各商品卖了多少，列出套餐待确认项。再比较近 7 天已完整同步的营业日，并结合我提供的现存库存、配方和供货周期，解释明天的备货建议。”</p><ul><li>先确认缺失和失败同步；空数据不能直接解释成当天没卖货。</li><li>按名称推断的拆分需要核对。京东数量是原始下单口径，售后尚未对账，不能作为最终净销量。</li><li>不同单位分别分析，商品销量不等于食材耗料。取消、退货与实际已制作消耗需要分开判断。</li></ul><p>完整安装与版本说明请查看工程的 <code>docs/mcp.md</code>；dsh 使用插件 YAML，不是通用 JSON 的 mcpServers 格式。当前环境未替你安装或改动 dsh。</p></div></section>`;
  }
  const agentUI = window.TakeawayAgentTasks({api, escape, field, openDialog, toast, main, loadPage, getLoadId: () => loadId});
  const loaders = {
    agents: (current) => agentUI.load(current),
    dashboard,
    orders,
    sales,
    rules,
    merchants,
    syncs,
    "mcp-guide": mcpGuide,
  };
  let loadId = 0;
  async function loadPage(quiet = false) {
    const current = ++loadId;
    clearError();
    updateDemoNotice();
    if (!quiet)
      main.innerHTML =
        '<div class="loading-state" role="status"><span class="spinner" aria-hidden="true"></span>正在读取数据…</div>';
    try {
      ensureDates();
      saveFilters();
      await loaders[page](current, quiet);
      if (current === loadId) icons(main);
    } catch (error) {
      if (current !== loadId) return;
      showError(error.message);
      if (!quiet)
        main.innerHTML = `<section class="panel">${empty("本次查询未完成", "请按上方错误说明检查配置，或稍后重新查询。")}<div class="button-row" style="justify-content:center;padding-bottom:25px"><button class="button secondary" data-action="refresh">重新查询</button></div></section>`;
    }
  }
  async function refreshMerchants() {
    const selected = $("#merchant-filter").value;
    state.merchants = (await api("/api/merchants")).items;
    $("#merchant-filter").innerHTML =
      '<option value="">全部门店</option>' + merchantOptions();
    if (state.merchants.some((item) => String(item.id) === selected))
      $("#merchant-filter").value = selected;
  }
  function addDays(value, amount) {
    const date = new Date(value + "T12:00:00Z");
    date.setUTCDate(date.getUTCDate() + amount);
    return date.toISOString().slice(0, 10);
  }
  async function action(event) {
    const button = event.target.closest("[data-action]");
    if (!button || button.disabled) return;
    const name = button.dataset.action;
    try {
      if (name === "today" || name === "week") {
        $("#end-date").value = state.session.today;
        $("#start-date").value =
          name === "week"
            ? addDays(state.session.today, -6)
            : state.session.today;
        state.page = 1;
        await loadPage();
      } else if (name === "refresh") await loadPage();
      else if (name === "new-agent-task") agentUI.edit();
      else if (name === "edit-agent-task") agentUI.edit(button.dataset.id);
      else if (name === "run-agent-task") await agentUI.run(button.dataset.id);
      else if (name === "agent-log") await agentUI.log(button.dataset.id);
      else if (name === "new-merchant") editMerchant();
      else if (name === "edit-merchant") editMerchant(button.dataset.id);
      else if (name === "new-rule") editRule();
      else if (name === "edit-rule") editRule(button.dataset.id);
      else if (name === "close-dialog") $("#editor").close();
      else if (name === "add-part") {
        if ($$(".split-row").length >= 50)
          throw new Error("每条规则最多50个子商品。");
        $("#rule-parts").insertAdjacentHTML("beforeend", rulePart());
        $("#rule-parts .split-row:last-child input").focus();
        $("#rule-form").dispatchEvent(new Event("input", { bubbles: true }));
      } else if (name === "remove-part") {
        const form = button.closest("form");
        button.closest(".split-row").remove();
        form.dispatchEvent(new Event("input", { bubbles: true }));
      } else if (name === "preview-rule") {
        button.disabled = true;
        await previewRule();
      } else if (name === "search-orders") searchOrders();
      else if (name === "toggle-order") {
        const target = $(`#order-detail-${button.dataset.id}`);
        target.hidden = !target.hidden;
        button.setAttribute("aria-expanded", String(!target.hidden));
      } else if (name === "previous-page" || name === "next-page") {
        state.page += name === "next-page" ? 1 : -1;
        await loadPage();
      } else if (name === "reprocess") {
        const id = Number($("#reprocess-merchant").value);
        if (!id) throw new Error("请选择需要重算的门店。");
        button.disabled = true;
        const result = await api(`/api/merchants/${id}/reprocess`, {
          method: "POST",
        });
        toast(`已按最新规则重算 ${result.processed} 笔订单的拆分明细。`);
      } else if (name === "sync-merchant") {
        const id = button.dataset.id;
        location.assign(
          `/syncs?merchant_id=${encodeURIComponent(id)}&start_date=${state.session.today}&end_date=${state.session.today}`,
        );
      } else if (name === "sync-current") {
        location.assign("/agents");
      } else if (name === "copy-config") {
        const text = button.dataset.kind === "http" ? httpConfig : stdioConfig;
        if (!navigator.clipboard)
          throw new Error("当前浏览器无法直接复制，请选中配置文本复制。");
        await navigator.clipboard.writeText(text);
        toast("配置示例已复制，请修改路径或域名并提供独立 token。");
      }
    } catch (error) {
      toast(error.message, true);
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  }
  document.addEventListener("click", action);
  $("#close-editor").addEventListener("click", () => $("#editor").close());
  $("#editor").addEventListener("click", (event) => {
    if (event.target === $("#editor")) {
      const rect = $("#editor").getBoundingClientRect();
      if (
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom
      )
        $("#editor").close();
    }
  });
  function toggleMenu(open) {
    const sidebar = $("#sidebar"),
      wasOpen = sidebar.classList.contains("open");
    sidebar.classList.toggle("open", open);
    sidebar.inert = !open && window.matchMedia("(max-width:850px)").matches;
    $("#sidebar-shade").hidden = !open;
    $("#menu-toggle").setAttribute("aria-expanded", String(open));
    $("#menu-toggle").setAttribute(
      "aria-label",
      open ? "关闭导航" : "打开导航",
    );
    if (open) $(".nav-link", sidebar).focus();
    else if (wasOpen) $("#menu-toggle").focus();
  }
  $("#menu-toggle").addEventListener("click", () =>
    toggleMenu(!$("#sidebar").classList.contains("open")),
  );
  $("#sidebar-shade").addEventListener("click", () => toggleMenu(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") toggleMenu(false);
  });
  window.addEventListener("resize", () => {
    if (!window.matchMedia("(max-width:850px)").matches) toggleMenu(false);
    else $("#sidebar").inert = !$("#sidebar").classList.contains("open");
  });
  toggleMenu(false);
  $("#logout").addEventListener("click", async () => {
    try {
      await api("/logout", { method: "POST" });
      location.assign("/login");
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#apply-filter").addEventListener("click", () => {
    state.page = 1;
    loadPage();
  });
  for (const selector of ["#merchant-filter", "#platform-filter"])
    $(selector).addEventListener("change", () => {
      if (selector === "#platform-filter") {
        const selected = state.merchants.find(
          (item) => String(item.id) === $("#merchant-filter").value,
        );
        if (
          selected &&
          $("#platform-filter").value &&
          selected.platform !== $("#platform-filter").value
        )
          $("#merchant-filter").value = "";
      }
      state.page = 1;
      loadPage();
    });
  async function init() {
    icons();
    const title = titles[page] || titles.dashboard;
    $("#page-title").textContent = title[0];
    $("#breadcrumb-title").textContent = title[0];
    $("#page-subtitle").textContent = title[1];
    document.title = `${title[0]} · 餐账`;
    try {
      state.session = await api("/api/session");
      await refreshMerchants();
      $("#start-date").value = params.get("start_date") || state.session.today;
      $("#end-date").value = params.get("end_date") || state.session.today;
      $("#merchant-filter").value = params.get("merchant_id") || "";
      $("#platform-filter").value = params.get("platform") || "";
      const dataPages = ["dashboard", "orders", "sales"];
      $("#filter-bar").hidden =
        !dataPages.includes(page) && !["rules", "syncs"].includes(page);
      if (!dataPages.includes(page)) {
        $(".date-filter").hidden = true;
        $(".filter-shortcuts").hidden = true;
      }
      if (dataPages.includes(page))
        $("#page-actions").innerHTML =
          `<button class="button secondary" data-action="sync-current">${icon("sync")}采集 Agent</button>${page === "dashboard" ? `<a class="button" data-preserve-filters href="/orders?${filters()}">${icon("orders")}查看订单</a>` : ""}`;
      await loadPage();
      if (["syncs", "agents"].includes(page))
        state.timer = setInterval(() => {
          if (
            document.visibilityState === "visible" &&
            !$("#editor").open &&
            !$("#sync-form")?.contains(document.activeElement)
          )
            loadPage(true);
        }, 8000);
    } catch (error) {
      showError(error.message);
      main.innerHTML = empty(
        "工作台未能读取数据",
        "请确认服务正常运行，再刷新页面。",
      );
    }
  }
  window.addEventListener("pagehide", () => clearInterval(state.timer));
  init();
})();
