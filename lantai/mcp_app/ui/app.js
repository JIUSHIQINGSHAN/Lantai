"use strict";

(() => {
  const UI_VERSION = "2026-01-26";
  const TOOL_SCOPE_FIELDS = ["user_id", "tenant_id", "agent_id", "federation_id"];
  const HOST_STYLE_KEYS = new Set([
    "--color-background-primary",
    "--color-background-secondary",
    "--color-background-tertiary",
    "--color-background-info",
    "--color-background-danger",
    "--color-background-success",
    "--color-background-warning",
    "--color-text-primary",
    "--color-text-secondary",
    "--color-text-info",
    "--color-text-danger",
    "--color-text-success",
    "--color-text-warning",
    "--color-border-primary",
    "--color-border-info",
    "--font-sans",
    "--font-mono",
    "--border-radius-sm",
  ]);
  const state = {
    hostContext: {},
    hostCapabilities: {},
    originTool: "",
    originalInput: {},
    inputReceived: false,
    initialized: false,
    started: false,
    nextId: 1,
    pending: new Map(),
    decision: null,
  };
  const $ = (selector) => document.querySelector(selector);

  function setStatus(text) {
    $("#status").textContent = text;
  }

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function sendRequest(method, params = {}) {
    const id = state.nextId++;
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        state.pending.delete(id);
        reject(new Error("宿主响应超时"));
      }, 20000);
      state.pending.set(id, { resolve, reject, timer });
      // The sandbox view has an opaque origin, so the host bridge requires '*'.
      window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
    });
  }

  function sendNotification(method, params = {}) {
    window.parent.postMessage({ jsonrpc: "2.0", method, params }, "*");
  }

  function applyHostContext(context) {
    if (!isRecord(context)) return;
    state.hostContext = context;
    const theme = context.theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    const variables = isRecord(context.styles) && isRecord(context.styles.variables)
      ? context.styles.variables
      : {};
    for (const key of HOST_STYLE_KEYS) document.documentElement.style.removeProperty(key);
    for (const [key, value] of Object.entries(variables)) {
      if (HOST_STYLE_KEYS.has(key) && typeof value === "string" && value.length <= 512) {
        document.documentElement.style.setProperty(key, value);
      }
    }
    const originName = context.toolInfo?.tool?.name;
    if (typeof originName === "string") state.originTool = originName;
    $("#connectionState").dataset.connected = "true";
    if (["crystals_list", "crystal_decide"].includes(state.originTool)) {
      $("#tab-crystals").hidden = false;
    }
  }

  function scopeArguments() {
    const scope = {};
    for (const key of TOOL_SCOPE_FIELDS) {
      const value = state.originalInput[key];
      if (typeof value === "string" && value.length > 0 && value.length <= 256) {
        scope[key] = value;
      }
    }
    return scope;
  }

  function canCallServerTools() {
    return Boolean(state.hostCapabilities.serverTools);
  }

  function updateControls() {
    const enabled = canCallServerTools();
    $("#searchSubmit").disabled = !enabled;
    for (const button of document.querySelectorAll("[data-refresh]")) button.disabled = !enabled;
    $("#refreshCrystals").disabled = !enabled;
  }

  function selectTab(name, focus = false) {
    for (const button of document.querySelectorAll("[role=tab]")) {
      const selected = button.dataset.tab === name && !button.hidden;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focus) button.focus();
    }
    for (const panel of document.querySelectorAll("[data-view]")) {
      panel.hidden = panel.dataset.view !== name;
    }
  }

  function textValue(value) {
    if (typeof value === "string") return value;
    if (value === null || value === undefined) return "";
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }

  function listFrom(value, keys) {
    if (Array.isArray(value)) return value;
    if (!isRecord(value)) return [];
    for (const key of keys) if (Array.isArray(value[key])) return value[key];
    return [];
  }

  function recordCard(title, body, extra = "") {
    const article = document.createElement("article");
    article.className = "record-card";
    const heading = document.createElement("h3");
    heading.textContent = textValue(title) || "未命名记录";
    article.append(heading);
    if (body) {
      const content = document.createElement("p");
      content.textContent = textValue(body);
      article.append(content);
    }
    if (extra) {
      const detail = document.createElement("pre");
      detail.textContent = textValue(extra);
      article.append(detail);
    }
    return article;
  }

  function renderRecords(container, items, emptyText, titleField, bodyFields) {
    container.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = emptyText;
      container.append(empty);
      return;
    }
    for (const entry of items) {
      if (!isRecord(entry)) continue;
      const item = isRecord(entry.memory) ? entry.memory : entry;
      let body = "";
      for (const key of bodyFields) {
        if (item[key] !== undefined && item[key] !== null) {
          body = item[key];
          break;
        }
      }
      container.append(recordCard(item[titleField] || item.id, body));
    }
  }

  function decodeToolResult(response) {
    if (response?.isError) throw new Error("工具调用失败");
    if (isRecord(response?.structuredContent)) return response.structuredContent;
    const text = Array.isArray(response?.content)
      ? response.content.find((part) => part?.type === "text")?.text
      : undefined;
    if (typeof text === "string") {
      try {
        return JSON.parse(text);
      } catch {
        return { text };
      }
    }
    return {};
  }

  async function callTool(name, args) {
    if (!canCallServerTools()) throw new Error("宿主未开放工具调用");
    const response = await sendRequest("tools/call", { name, arguments: args });
    return decodeToolResult(response);
  }

  async function loadCoreMemory() {
    const args = scopeArguments();
    if (typeof state.originalInput.namespace === "string") {
      args.namespace = state.originalInput.namespace;
    }
    const result = await callTool("core_memory_get", args);
    renderRecords($("#coreBlocks"), listFrom(result, ["blocks"]), "暂无核心记忆", "block", ["content"]);
  }

  async function loadRecentMemory() {
    const result = await callTool("mem_recent", { ...scopeArguments(), limit: 20 });
    renderRecords(
      $("#recentMemories"),
      listFrom(result, ["memories", "items", "results"]),
      "暂无最近记忆",
      "title",
      ["content", "summary", "text"],
    );
  }

  async function loadOverview() {
    setStatus("正在读取记忆…");
    const results = await Promise.allSettled([loadCoreMemory(), loadRecentMemory()]);
    const failure = results.find((result) => result.status === "rejected");
    if (failure) throw failure.reason;
    setStatus("记忆总览已更新");
  }

  async function refreshPanel(load) {
    setStatus("正在读取记忆…");
    try {
      await load();
      setStatus("记忆已更新");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "读取记忆失败");
    }
  }

  async function runSearch(query) {
    const normalized = query.trim();
    if (!normalized) {
      $("#searchQuery").focus();
      return;
    }
    $("#searchSubmit").disabled = true;
    setStatus("正在检索…");
    try {
      const result = await callTool("search", {
        ...scopeArguments(),
        query: normalized,
        top_k: 5,
      });
      renderRecords(
        $("#searchResults"),
        listFrom(result, ["results", "memories", "items"]),
        "没有检索结果",
        "title",
        ["content", "summary", "text"],
      );
      setStatus("检索完成");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "检索失败");
    } finally {
      $("#searchSubmit").disabled = !canCallServerTools();
    }
  }

  function renderCrystals(result) {
    const container = $("#crystalCandidates");
    container.replaceChildren();
    const items = listFrom(result, ["crystals", "items", "candidates"]);
    state.crystals = items;
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "当前没有待审结晶";
      container.append(empty);
      return;
    }
    for (const crystal of items) {
      if (!isRecord(crystal)) continue;
      const card = recordCard(
        crystal.skill_name || crystal.title || crystal.id,
        crystal.trigger_rule || crystal.summary || crystal.status,
        crystal.procedure,
      );
      const status = document.createElement("p");
      status.className = "muted";
      status.textContent = `状态：${textValue(crystal.status || "candidate")}`;
      card.append(status);
      if (typeof crystal.id === "string" && crystal.status !== "approved" && crystal.status !== "archived") {
        const actions = document.createElement("div");
        actions.className = "candidate-actions";
        for (const decision of ["approve", "reject"]) {
          const button = document.createElement("button");
          button.type = "button";
          button.dataset.decision = decision;
          button.dataset.id = crystal.id;
          button.textContent = decision === "approve" ? "批准" : "拒绝并归档";
          if (decision === "reject") button.setAttribute("aria-label", `拒绝并归档 ${textValue(crystal.skill_name || crystal.id)}`);
          actions.append(button);
        }
        card.append(actions);
      }
      container.append(card);
    }
  }

  async function loadCrystals() {
    const result = await callTool("crystals_list", {
      ...scopeArguments(),
      status: "candidate",
      limit: 50,
    });
    renderCrystals(result);
  }

  function openDecision(candidateId, approve) {
    const candidate = state.crystals?.find((item) => item.id === candidateId);
    if (!candidate) return;
    state.decision = { candidateId, approve };
    $("#decisionTitle").textContent = approve ? "批准结晶候选" : "拒绝并归档候选";
    $("#decisionSummary").textContent = `候选：${textValue(candidate.skill_name || candidate.id)}`;
    $("#stepsLabel").hidden = !approve;
    $("#decisionSteps").hidden = !approve;
    $("#reasonLabel").hidden = approve;
    $("#decisionReason").hidden = approve;
    $("#decisionSteps").value = "";
    $("#decisionReason").value = "";
    $("#decisionConfirm").textContent = approve ? "确认批准" : "确认拒绝";
    $("#decisionDialog").showModal();
    (approve ? $("#decisionSteps") : $("#decisionReason")).focus();
  }

  async function submitDecision(event) {
    event?.preventDefault();
    if (!state.decision) return;
    const { candidateId, approve } = state.decision;
    let steps = [];
    let reason = "";
    if (approve) {
      steps = $("#decisionSteps").value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
      if (!steps.length) {
        $("#decisionSteps").setCustomValidity("批准結晶前至少填写一步技能步骤");
        $("#decisionSteps").reportValidity();
        $("#decisionSteps").focus();
        return;
      }
      $("#decisionSteps").setCustomValidity("");
    } else {
      reason = $("#decisionReason").value.trim();
      if (!reason) {
        $("#decisionReason").setCustomValidity("拒绝结晶必须填写理由");
        $("#decisionReason").reportValidity();
        $("#decisionReason").focus();
        return;
      }
      $("#decisionReason").setCustomValidity("");
    }
    $("#decisionConfirm").disabled = true;
    try {
      await callTool("crystal_decide", {
        ...scopeArguments(),
        crystal_id: candidateId,
        approve,
        steps,
        reason,
      });
      $("#decisionDialog").close();
      setStatus(approve ? "结晶已批准" : "结晶已拒绝并归档");
      await loadCrystals();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "裁决失败");
    } finally {
      $("#decisionConfirm").disabled = false;
    }
  }

  function renderInitialResult(params) {
    const response = isRecord(params?.result) ? params.result : params;
    let payload;
    try {
      payload = decodeToolResult(response);
    } catch {
      return;
    }
    if (state.originTool === "core_memory_get") {
      renderRecords($("#coreBlocks"), listFrom(payload, ["blocks"]), "暂无核心记忆", "block", ["content"]);
    } else if (state.originTool === "mem_recent") {
      renderRecords($("#recentMemories"), listFrom(payload, ["memories", "items"]), "暂无最近记忆", "title", ["content", "summary"]);
    } else if (state.originTool === "search") {
      renderRecords($("#searchResults"), listFrom(payload, ["results", "items"]), "没有检索结果", "title", ["content", "summary"]);
      selectTab("search");
    } else if (state.originTool === "crystals_list") {
      state.crystals = listFrom(payload, ["crystals", "items", "candidates"]);
      renderCrystals(payload);
    }
  }

  async function startPanel() {
    if (!state.initialized || !state.inputReceived || state.started) return;
    state.started = true;
    updateControls();
    if (!canCallServerTools()) {
      setStatus("宿主未开放工具调用，面板只展示当前工具结果。");
      return;
    }
    try {
      await loadOverview();
      if (["crystals_list", "crystal_decide"].includes(state.originTool)) {
        selectTab("crystals");
        await loadCrystals();
      } else if (state.originTool === "search") {
        selectTab("search");
        if (typeof state.originalInput.query === "string") {
          $("#searchQuery").value = state.originalInput.query;
          await runSearch(state.originalInput.query);
        }
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "读取记忆失败");
    }
  }

  function handleHostMessage(event) {
    if (event.source !== window.parent || !isRecord(event.data)) return;
    const message = event.data;
    if (message.jsonrpc !== "2.0") return;
    if (message.id !== undefined && state.pending.has(message.id)) {
      const pending = state.pending.get(message.id);
      state.pending.delete(message.id);
      window.clearTimeout(pending.timer);
      if (message.error) pending.reject(new Error("宿主工具调用失败"));
      else pending.resolve(message.result || {});
      return;
    }
    if (typeof message.method !== "string") return;
    const params = isRecord(message.params) ? message.params : {};
    if (message.method === "ui/notifications/host-context-changed") {
      applyHostContext(params.hostContext || params);
    } else if (message.method === "ui/notifications/tool-input") {
      state.originalInput = isRecord(params.arguments) ? params.arguments : {};
      state.inputReceived = true;
      void startPanel();
    } else if (message.method === "ui/notifications/tool-result") {
      renderInitialResult(params);
    }
  }

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const tab = target.closest("[data-tab]");
    if (tab && !tab.hidden) selectTab(tab.dataset.tab);
    const decision = target.closest("[data-decision]");
    if (decision) openDecision(decision.dataset.id, decision.dataset.decision === "approve");
    const cancel = target.closest("[data-cancel-decision]");
    if (cancel) $("#decisionDialog").close();
    const refresh = target.closest("[data-refresh]");
    if (refresh && canCallServerTools()) {
      void refreshPanel(refresh.dataset.refresh === "overview" ? loadCoreMemory : loadRecentMemory);
    }
    if (target.closest("#refreshCrystals") && canCallServerTools()) void refreshPanel(loadCrystals);
  });

  $("#panelTabs").addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...document.querySelectorAll("[role=tab]")].filter((tab) => !tab.hidden);
    const current = tabs.indexOf(document.activeElement);
    const next = event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    selectTab(tabs[next].dataset.tab, true);
  });

  $("#searchSubmit").addEventListener("click", () => {
    void runSearch($("#searchQuery").value);
  });
  $("#searchForm").addEventListener("submit", (event) => {
    event.preventDefault();
    void runSearch($("#searchQuery").value);
  });
  $("#searchQuery").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    void runSearch($("#searchQuery").value);
  });
  $("#decisionConfirm").addEventListener("click", () => void submitDecision());
  $("#decisionForm").addEventListener("submit", (event) => void submitDecision(event));
  $("#decisionDialog").addEventListener("close", () => {
    if (!$("#decisionDialog").open) state.decision = null;
    $("#decisionSteps").setCustomValidity("");
    $("#decisionReason").setCustomValidity("");
  });

  window.addEventListener("message", handleHostMessage);
  sendRequest("ui/initialize", {
    protocolVersion: UI_VERSION,
    appInfo: { name: "lantai-memory-panel", version: "0.69.1" },
    appCapabilities: {},
  }).then((result) => {
    state.hostCapabilities = isRecord(result.hostCapabilities) ? result.hostCapabilities : {};
    applyHostContext(result.hostContext);
    state.initialized = true;
    $("#connectionState").setAttribute("aria-label", "已连接宿主");
    sendNotification("ui/notifications/initialized");
    setStatus("等待宿主传入当前工具上下文…");
    void startPanel();
  }).catch((error) => {
    $("#connectionState").setAttribute("aria-label", "宿主连接失败");
    setStatus(error instanceof Error ? error.message : "宿主连接失败");
  });
})();
