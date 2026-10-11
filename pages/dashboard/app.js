// astrbot_plugin_cbm —— 控制面板前端逻辑
// 通过 window.AstrBotPluginPage bridge 调用插件后端 Web API。

const bridge = window.AstrBotPluginPage;

const $ = (id) => document.getElementById(id);
const state = { variant: "ui", installPolling: null };

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

function fmtUptime(sec) {
  if (!sec && sec !== 0) return "—";
  sec = Math.floor(sec);
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const parts = [];
  if (d) parts.push(d + " 天");
  if (h) parts.push(h + " 小时");
  if (m) parts.push(m + " 分");
  if (!d && !h) parts.push(s + " 秒");
  return parts.join(" ");
}

function busy(btn, on) {
  if (!btn) return;
  btn.disabled = on;
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

function renderStatus(s) {
  const running = !!s.running;
  const pill = $("run-pill");
  pill.classList.toggle("on", running);
  pill.classList.toggle("off", !running && s.installed);
  $("run-pill-text").textContent = running
    ? "运行中"
    : s.installed
      ? "已停止"
      : "未安装";

  const st = $("st-state");
  st.textContent = running ? "运行中" : s.installed ? "已停止" : "未安装";
  st.className = running ? "ok" : s.installed ? "" : "off";

  $("st-version").textContent = s.binary_version || "—";
  $("st-pid").textContent = running && s.pid ? s.pid : "—";
  $("st-port").textContent = s.port || "—";
  $("st-uptime").textContent = running ? fmtUptime(s.uptime) : "—";
  $("st-cache").textContent = s.cache_dir || "—";
  $("st-cache").title = s.cache_dir || "";

  const vt = s.variant ? (s.variant === "ui" ? "UI 版" : "标准版") : "—";
  $("variant-tag").textContent = s.installed ? vt : "未安装";

  const uiLink = $("link-ui");
  if (running && s.variant === "ui" && s.ui_url) {
    uiLink.href = s.ui_url;
    uiLink.classList.remove("hidden");
  } else {
    uiLink.classList.add("hidden");
  }

  $("subtitle").textContent = s.installed
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

  // 按钮可用性
  const hasInstalled = !!s.installed;
  $("btn-start").disabled = !hasInstalled || running;
  $("btn-stop").disabled = !running;
  $("btn-restart").disabled = !hasInstalled;
  $("btn-uninstall").disabled = !hasInstalled;
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
    }
  }, 900);
}

async function doInstall() {
  const version = $("in-version").value.trim() || "latest";
  const mirror = ($("in-mirror") ? $("in-mirror").value.trim() : "");
  const btn = $("btn-install");
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
  if (!confirm("确定卸载 CBM？（可保留索引数据）")) return;
  const purge = confirm("是否同时删除索引数据缓存？\n点“确定”删除，点“取消”保留。");
  const btn = $("btn-uninstall");
  busy(btn, true);
  try {
    await bridge.apiPost("uninstall", { purge_data: purge });
    toast("已卸载 CBM", "ok");
    refreshStatus();
  } catch (e) {
    toast("卸载失败：" + e.message, "err");
  } finally {
    busy(btn, false);
  }
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

// --------------------------- 配置 --------------------------- //
async function loadConfig() {
  const box = $("config-list");
  box.innerHTML = '<p class="cbm-empty">读取中…</p>';
  try {
    const data = await bridge.apiGet("config");
    renderConfig(data);
  } catch (e) {
    box.innerHTML =
      '<p class="cbm-empty">读取失败：' + e.message + "</p>";
  }
}

function renderConfig(data) {
  const box = $("config-list");
  const items = data.items || {};
  const keys = Object.keys(items);
  if (!keys.length) {
    box.innerHTML = '<p class="cbm-empty">暂无可用配置项。</p>';
    return;
  }
  box.innerHTML = "";
  keys.forEach((key) => {
    const value = items[key];
    const type = (data.known && data.known[key]) || typeof value;
    const row = document.createElement("div");
    row.className = "config-row";

    const name = document.createElement("span");
    name.className = "config-key";
    name.innerHTML = key + "<small>" + type + "</small>";

    const ctrl = document.createElement("div");
    ctrl.className = "config-ctrl";

    let getter;
    if (type === "bool") {
      const sw = document.createElement("label");
      sw.className = "switch";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!value;
      const sl = document.createElement("span");
      sl.className = "slider";
      sw.appendChild(input);
      sw.appendChild(sl);
      ctrl.appendChild(sw);
      getter = () => input.checked;
      input.addEventListener("change", () => saveConfig(key, input.checked, input));
    } else {
      const input = document.createElement("input");
      input.type = type === "int" ? "number" : "text";
      input.value = value;
      ctrl.appendChild(input);
      getter = () => (type === "int" ? parseInt(input.value, 10) : input.value);
      const save = document.createElement("button");
      save.className = "cbm-btn mini";
      save.textContent = "保存";
      save.addEventListener("click", () => saveConfig(key, getter(), save));
      ctrl.appendChild(save);
    }

    const reset = document.createElement("button");
    reset.className = "cbm-btn mini ghost";
    reset.textContent = "重置";
    reset.title = "恢复默认值";
    reset.addEventListener("click", () => resetConfig(key));
    ctrl.appendChild(reset);

    row.appendChild(name);
    row.appendChild(ctrl);
    box.appendChild(row);
  });
}

async function saveConfig(key, value, ctrl) {
  try {
    await bridge.apiPost("config", { key, value });
    toast(`已保存 ${key}`, "ok");
  } catch (e) {
    toast(`保存 ${key} 失败：` + e.message, "err");
  }
}

async function resetConfig(key) {
  try {
    await bridge.apiPost("config/reset", { key });
    toast(`已重置 ${key}`, "ok");
    loadConfig();
  } catch (e) {
    toast(`重置 ${key} 失败：` + e.message, "err");
  }
}

// --------------------------- 日志 --------------------------- //
async function loadLogs() {
  const box = $("log-box");
  box.textContent = "加载中…";
  try {
    const data = await bridge.apiGet("logs", { lines: 300 });
    box.textContent = data.lines || "（暂无日志）";
    box.scrollTop = box.scrollHeight;
  } catch (e) {
    box.textContent = "读取日志失败：" + e.message;
  }
}

// --------------------------- 绑定 --------------------------- //
function bind() {
  $("btn-refresh").addEventListener("click", refreshStatus);
  $("btn-start").addEventListener("click", () => doRun("start"));
  $("btn-stop").addEventListener("click", () => doRun("stop"));
  $("btn-restart").addEventListener("click", () => doRun("restart"));
  $("btn-install").addEventListener("click", doInstall);
  $("btn-uninstall").addEventListener("click", doUninstall);
  $("btn-load-config").addEventListener("click", loadConfig);
  $("btn-load-logs").addEventListener("click", loadLogs);

  document.querySelectorAll("#seg-variant .seg-item").forEach((el) => {
    el.addEventListener("click", () => setVariant(el.dataset.value));
  });

  if ($("btn-reload-versions")) {
    $("btn-reload-versions").addEventListener("click", () =>
      loadVersions(true),
    );
  }
  loadVersions(false);
  loadMirrorDefault();
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
    // 保留 latest，追加 releases
    sel.innerHTML = '<option value="latest">latest（最新稳定版）</option>';
    const list = v.releases || [];
    list.forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r.version;
      opt.textContent =
        "v" + r.version + (r.prerelease ? "（预发布）" : "");
      sel.appendChild(opt);
    });
    // 恢复原选择（若仍存在）
    if ([...sel.options].some((o) => o.value === current)) {
      sel.value = current;
    }
    if (v.latest) {
      const first = sel.options[0];
      if (first) first.textContent = `latest（最新稳定版 v${v.latest}）`;
    }
  } catch (e) {
    /* 静默：仍可手动输入 latest */
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
