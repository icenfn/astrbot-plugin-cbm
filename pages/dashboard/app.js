// astrbot_plugin_cbm —— 控制面板前端逻辑
// 通过 window.AstrBotPluginPage bridge 调用插件后端 Web API。

const bridge = window.AstrBotPluginPage;

const $ = (id) => document.getElementById(id);
const state = { variant: "ui", installPolling: null, uiUrl: null };

// --------------------------- 通用工具 --------------------------- //
function toast(msg, type = "") {
  const wrap = $("toasts");
  const el = document.createElement("div");
  el.className = "cbm-toast " + type;
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transform = "translateY(8px)";
    setTimeout(() => el.remove(), 220);
  }, 3200);
}

function busy(btn, on) {
  if (!btn) return;
  btn.disabled = on;
}

// --------------------------- 二次确认弹窗 --------------------------- //
// AstrBot 插件页面运行在受限 iframe 中，原生 confirm() 常被拦截，改用自定义 modal。
function confirmModal({ title, message, okText = "确定", cancelText = "取消", danger = true, checkbox = null }) {
  return new Promise((resolve) => {
    const mask = $("modal-mask");
    $("modal-title").textContent = title || "确认";
    $("modal-body").textContent = message || "";
    const ok = $("modal-ok");
    ok.textContent = okText;
    ok.className = "cbm-btn " + (danger ? "danger" : "primary");
    $("modal-cancel").textContent = cancelText;

    const wrap = $("modal-check-wrap");
    const chk = $("modal-check");
    if (checkbox) {
      wrap.classList.remove("hidden");
      $("modal-check-label").textContent = checkbox.label;
      chk.checked = !!checkbox.checked;
    } else {
      wrap.classList.add("hidden");
      chk.checked = false;
    }

    mask.classList.remove("hidden");

    const cleanup = () => {
      mask.classList.add("hidden");
      ok.removeEventListener("click", onOk);
      $("modal-cancel").removeEventListener("click", onCancel);
      mask.removeEventListener("click", onMask);
      document.removeEventListener("keydown", onKey);
    };
    const onOk = () => {
      cleanup();
      resolve({ ok: true, checked: chk.checked });
    };
    const onCancel = () => {
      cleanup();
      resolve({ ok: false, checked: false });
    };
    const onMask = (e) => {
      if (e.target === mask) onCancel();
    };
    const onKey = (e) => {
      if (e.key === "Escape") onCancel();
    };

    ok.addEventListener("click", onOk);
    $("modal-cancel").addEventListener("click", onCancel);
    mask.addEventListener("click", onMask);
    document.addEventListener("keydown", onKey);
  });
}

// --------------------------- 状态渲染 --------------------------- //
async function refreshStatus() {
  try {
    const s = await bridge.apiGet("status");
    renderStatus(s);
    return s;
  } catch (e) {
    toast("读取状态失败：" + e.message, "err");
  }
}

function buildUiUrl(s) {
  if (!s.ui_proxy || !s.ui_proxy.running || !s.ui_proxy.port) return null;
  const host = location.hostname || "127.0.0.1";
  return `http://${host}:${s.ui_proxy.port}/`;
}

function renderStatus(s) {
  const running = !!s.running;
  const installed = !!s.installed;

  const pill = $("run-pill");
  pill.classList.toggle("on", running);
  pill.classList.toggle("off", !running && installed);
  $("run-pill-text").textContent = running ? "运行中" : installed ? "已停止" : "未安装";

  const st = $("st-state");
  st.textContent = running ? "运行中" : installed ? "已停止" : "未安装";
  st.className = running ? "ok" : installed ? "" : "off";

  $("st-version").textContent = s.binary_version || "—";
  $("st-pid").textContent = running && s.pid ? s.pid : "—";
  $("st-port").textContent = s.port || "—";
  $("st-cache").textContent = s.cache_dir || "—";
  $("st-cache").title = s.cache_dir || "";

  const vt = s.variant ? (s.variant === "ui" ? "UI 版" : "标准版") : "—";
  $("variant-tag").textContent = installed ? vt : "未安装";

  $("subtitle").textContent = installed
    ? `已安装 v${s.binary_version || "?"} · ${s.platform.os}/${s.platform.arch}`
    : "尚未安装 CBM";

  // 同步安装区
  if (s.install_info && s.install_info.variant) {
    setVariant(s.install_info.variant, false);
  }
  const info = s.install_info || {};
  $("version-hint").textContent = info.version
    ? `已安装：${info.variant === "ui" ? "UI 版" : "标准版"} v${info.version}` +
      (info.sha256_verified ? "（SHA-256 已校验）" : "")
    : "已安装：—";

  // 安装 / 卸载 分开显示：未安装 -> 安装；已安装 -> 重新安装 / 更新 + 卸载
  $("btn-install").classList.toggle("hidden", installed);
  $("btn-reinstall").classList.toggle("hidden", !installed);
  $("btn-uninstall").classList.toggle("hidden", !installed);
  $("install-hint").textContent = installed
    ? "已安装。可重新安装 / 更新以切换版本或版本类型。"
    : "尚未安装，点击“安装”即可下载。";

  // 按钮可用性
  $("btn-start").disabled = !installed || running;
  $("btn-stop").disabled = !running;
  $("btn-restart").disabled = !installed;

  // WebUI 地址（经插件反向代理对外暴露；CBM 自身仅监听 127.0.0.1）
  const uiUrl = running && s.variant === "ui" ? buildUiUrl(s) : null;
  state.uiUrl = uiUrl;
  const tag = $("ui-state-tag");
  const input = $("ui-url-input");
  const copyBtn = $("btn-copy-url");
  const openLink = $("btn-open-ui");
  const hint = $("ui-hint");
  if (uiUrl) {
    $(`ui-url-row`).classList.remove("hidden");
    input.value = uiUrl;
    openLink.href = uiUrl;
    tag.textContent = "可访问";
    hint.classList.remove("warn");
    hint.textContent = "复制上方地址在浏览器打开（CBM 面板无法内嵌到当前页面）。";
  } else {
    input.value = "";
    openLink.removeAttribute("href");
    if (running && s.variant === "ui") {
      tag.textContent = "代理未就绪";
      hint.classList.add("warn");
      hint.textContent = "可视化代理未就绪（可能端口被占用），远程暂无法访问。";
    } else if (running) {
      tag.textContent = "标准版无面板";
      hint.classList.remove("warn");
      hint.textContent = "当前为标准版，无可视化面板。";
    } else {
      tag.textContent = installed ? "已停止" : "未安装";
      hint.classList.remove("warn");
      hint.textContent = installed
        ? "CBM 未运行，启动后将在此显示可视化地址。"
        : "尚未安装 CBM。";
    }
  }
  if (copyBtn) copyBtn.disabled = !uiUrl;
  if (openLink) openLink.classList.toggle("disabled", !uiUrl);
}

// --------------------------- 版本类型选择 --------------------------- //
function setVariant(v, persist = true) {
  state.variant = v === "ui" ? "ui" : "standard";
  document.querySelectorAll("#seg-variant .seg-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.value === state.variant);
  });
}

// --------------------------- 安装 --------------------------- //
function startInstallPolling() {
  if (state.installPolling) return;
  $("install-progress").classList.remove("hidden");
  state.installPolling = setInterval(async () => {
    try {
      const p = await bridge.apiGet("install/progress");
      const pct = p.percent || 0;
      $("bar-fill").style.width = pct + "%";
      let text = p.phase || "处理中";
      if (p.total) {
        const mb = (n) => (n / 1048576).toFixed(1);
        text += ` ${mb(p.downloaded)} / ${mb(p.total)} MB（${pct}%）`;
      }
      if (p.error) text = "失败：" + p.error;
      $("progress-text").textContent = text;

      if (!p.running) {
        clearInterval(state.installPolling);
        state.installPolling = null;
        $("btn-install").disabled = false;
        $("btn-reinstall").disabled = false;
        if (p.error) {
          toast("安装失败：" + p.error, "err");
        } else {
          toast("安装完成", "ok");
          $("bar-fill").style.width = "100%";
        }
        setTimeout(() => $("install-progress").classList.add("hidden"), 1600);
        refreshStatus();
      }
    } catch (e) {
      clearInterval(state.installPolling);
      state.installPolling = null;
      $("btn-install").disabled = false;
      $("btn-reinstall").disabled = false;
    }
  }, 900);
}

async function doInstall() {
  const version = $("in-version").value.trim() || "latest";
  const mirror = $("in-mirror") ? $("in-mirror").value.trim() : "";
  const btn = $("btn-install").classList.contains("hidden") ? $("btn-reinstall") : $("btn-install");
  busy(btn, true);
  try {
    await bridge.apiPost("install", {
      variant: state.variant,
      version,
      mirror,
    });
    toast("已开始安装，请稍候…");
    startInstallPolling();
  } catch (e) {
    busy(btn, false);
    toast("无法开始安装：" + e.message, "err");
  }
}

async function doUninstall() {
  const res = await confirmModal({
    title: "卸载 CBM",
    message: "确定要卸载 CBM 吗？卸载后需重新安装才能使用。",
    okText: "卸载",
    checkbox: { label: "同时删除索引数据缓存（不可恢复）", checked: false },
  });
  if (!res.ok) return;

  const btn = $("btn-uninstall");
  busy(btn, true);
  try {
    await bridge.apiPost("uninstall", { purge_data: res.checked });
    toast("已卸载 CBM", "ok");
    refreshStatus();
  } catch (e) {
    toast("卸载失败：" + e.message, "err");
  } finally {
    busy(btn, false);
  }
}

// --------------------------- 可视化 --------------------------- //
function copyUiUrl() {
  const input = $("ui-url-input");
  if (!state.uiUrl) return;
  let ok = false;
  try {
    input.select();
    ok = document.execCommand("copy");
  } catch (e) {
    ok = false;
  }
  if (!ok && navigator.clipboard) {
    navigator.clipboard.writeText(state.uiUrl).then(
      () => toast("已复制可视化地址", "ok"),
      () => toast("复制失败，请手动选择复制", "err"),
    );
    return;
  }
  toast(ok ? "已复制可视化地址" : "复制失败，请手动选择复制", ok ? "ok" : "err");
}

// --------------------------- 运行控制 --------------------------- //
async function doRun(action) {
  const map = { start: "btn-start", stop: "btn-stop", restart: "btn-restart" };
  const btn = $(map[action]);
  busy(btn, true);
  try {
    await bridge.apiPost(action, { variant: state.variant });
    toast(
      action === "start" ? "已启动" : action === "stop" ? "已停止" : "已重启",
      "ok",
    );
    setTimeout(refreshStatus, 600);
  } catch (e) {
    toast("操作失败：" + e.message, "err");
  } finally {
    busy(btn, false);
  }
}

// --------------------------- MCP 接入 --------------------------- //
async function loadMcp() {
  const box = $("mcp-snippet");
  box.textContent = "加载中…";
  try {
    const data = await bridge.apiGet("mcp");
    state.mcpSnippet = data.snippet || "";
    box.textContent = data.snippet || "（无法生成配置）";
    const st = $("mcp-status");
    if (st) st.dataset.path = data.config_path || "";
  } catch (e) {
    box.textContent = "读取失败：" + e.message;
  }
}

async function copyMcp() {
  if (!state.mcpSnippet) await loadMcp();
  if (!state.mcpSnippet) return;
  let ok = false;
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(state.mcpSnippet);
      ok = true;
    } catch (e) {
      ok = false;
    }
  }
  toast(ok ? "已复制 MCP 配置" : "复制失败，请手动选择复制", ok ? "ok" : "err");
}

async function addMcp() {
  const btn = $("btn-mcp-add");
  busy(btn, true);
  try {
    const res = await bridge.apiPost("mcp/add", {});
    toast("已写入 AstrBot MCP 配置：" + (res.path || ""), "ok");
    const st = $("mcp-status");
    if (st) st.textContent = "已添加到 " + (res.path || "AstrBot mcp_server.json") + "，重启 AstrBot 后生效。";
  } catch (e) {
    toast("添加失败：" + e.message + "（可复制配置手动添加）", "err");
  } finally {
    busy(btn, false);
  }
}

// --------------------------- 日志 --------------------------- //
async function loadLogs(silent = false) {
  const box = $("log-box");
  if (!silent) box.textContent = "加载中…";
  try {
    const data = await bridge.apiGet("logs", { lines: 300 });
    box.textContent = data.lines || "（暂无日志）";
    box.scrollTop = box.scrollHeight;
  } catch (e) {
    if (!silent) box.textContent = "读取日志失败：" + e.message;
  }
}

async function clearLogs() {
  const res = await confirmModal({
    title: "清除日志",
    message: "确定要清空当前运行日志吗？（不影响 CBM 运行）",
    okText: "清除",
    danger: true,
  });
  if (!res.ok) return;
  try {
    await bridge.apiPost("logs/clear", {});
    toast("日志已清除", "ok");
    loadLogs();
  } catch (e) {
    toast("清除失败：" + e.message, "err");
  }
}

function setLogAutoRefresh(on) {
  if (state.logTimer) {
    clearInterval(state.logTimer);
    state.logTimer = null;
  }
  if (on) {
    state.logTimer = setInterval(() => {
      if (!document.hidden) loadLogs(true);
    }, 5000);
  }
}

// --------------------------- 绑定 --------------------------- //
function bind() {
  $("btn-refresh").addEventListener("click", refreshStatus);
  $("btn-start").addEventListener("click", () => doRun("start"));
  $("btn-stop").addEventListener("click", () => doRun("stop"));
  $("btn-restart").addEventListener("click", () => doRun("restart"));
  $("btn-install").addEventListener("click", doInstall);
  $("btn-reinstall").addEventListener("click", doInstall);
  $("btn-uninstall").addEventListener("click", doUninstall);
  $("btn-open-ui").addEventListener("click", (e) => {
    if (!state.uiUrl) e.preventDefault();
  });
  $("btn-copy-url").addEventListener("click", copyUiUrl);
  $("btn-load-logs").addEventListener("click", () => loadLogs(false));
  $("btn-clear-logs").addEventListener("click", clearLogs);
  $("auto-logs").addEventListener("change", (e) => setLogAutoRefresh(e.target.checked));
  $("btn-mcp-load").addEventListener("click", loadMcp);
  $("btn-mcp-copy").addEventListener("click", copyMcp);
  $("btn-mcp-add").addEventListener("click", addMcp);

  document.querySelectorAll("#seg-variant .seg-item").forEach((el) => {
    el.addEventListener("click", () => setVariant(el.dataset.value));
  });

  if ($("btn-reload-versions")) {
    $("btn-reload-versions").addEventListener("click", () => loadVersions(true));
  }
  loadVersions(false);
  loadMirrorDefault();
  loadLogs(true);
  setLogAutoRefresh($("auto-logs").checked);
}

// 加载可用版本列表到下拉框
async function loadVersions(force) {
  if (state._versionsLoaded && !force) return;
  state._versionsLoaded = true;
  const sel = $("in-version");
  if (!sel) return;
  try {
    const v = await bridge.apiGet("versions");
    const current = sel.value;
    sel.innerHTML = '<option value="latest">latest（最新稳定版）</option>';
    const list = v.releases || [];
    list.forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r.version;
      opt.textContent = "v" + r.version + (r.prerelease ? "（预发布）" : "");
      sel.appendChild(opt);
    });
    if ([...sel.options].some((o) => o.value === current)) {
      sel.value = current;
    }
    if (v.latest) {
      const first = sel.options[0];
      if (first) first.textContent = `latest（最新稳定版 v${v.latest}）`;
    }
  } catch (e) {
    /* 静默：仍可手动选择 latest */
  }
}

// 读取插件默认镜像填入输入框，保证安装时使用默认值
async function loadMirrorDefault() {
  const box = $("in-mirror");
  if (!box || box.value) return;
  try {
    const v = await bridge.apiGet("status");
    if (v && v.install_info && v.install_info.mirror) {
      box.value = v.install_info.mirror;
    }
  } catch (e) {
    /* 忽略 */
  }
  if (!box.value) box.value = "https://gh-proxy.com/";
}

// --------------------------- 启动 --------------------------- //
(async function main() {
  try {
    await bridge.ready();
  } catch (e) {
    /* bridge 未注入时仍尝试渲染 */
  }
  bind();
  refreshStatus();
  // 每 5 秒轮询一次状态
  setInterval(() => {
    if (!document.hidden) refreshStatus();
  }, 5000);
})();
