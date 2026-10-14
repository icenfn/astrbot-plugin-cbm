# astrbot_plugin_cbm

> 在 AstrBot WebUI 中一站式管理 **Codebase Memory MCP**（CBM）—— 面向 AI 编码代理的代码记忆 / 知识图谱引擎，无需登录服务器敲命令。

面板采用 **标签页** 布局（概览 / 安装 / MCP / 调优 / 安全 / 日志）。

## ✨ 功能

| 功能 | 说明 |
| --- | --- |
| **安装 / 卸载** | 可选 **UI 版**（内置 3D 可视化面板，**默认**）或 **标准版**（无界面 MCP 服务）；版本号可下拉选择；自动下载并强制校验 **SHA-256**。未安装显示「安装」，已安装显示「重新安装 / 更新」与「卸载」。 |
| **下载镜像** | 默认 `https://gh-proxy.com/` 加速下载，可自定义；留空则直连 GitHub。 |
| **运行控制** | 一键 **启动 / 停止 / 重启**，实时查看状态。 |
| **可视化访问** | 内置反向代理，把 CBM 面板对外暴露，远程浏览器也能打开 3D 图谱。 |
| **访问鉴权** | 为可视化反向代理开启 **HTTP Basic Auth**，暴露公网时需用户名/密码才能访问。 |
| **接入 AstrBot MCP（stdio）** | 一键把 CBM 写入 AstrBot 的 MCP 配置（`data/mcp_server.json`），或复制 JSON 手动添加。 |
| **Streamable-HTTP MCP 端点** | CBM 仅支持 stdio MCP；开启后插件以常驻 stdio 子进程把它桥接为标准 **Streamable-HTTP** 端点（默认关闭），供远程 MCP 客户端连接。**强制 Bearer Token 鉴权**。 |
| **运行时调优** | 在面板「调优」页设置 CBM 的内存预算 `budget_mb`、并行任务数 `physical_job_limit` 等，启动/重启后自动注入生效。 |
| **日志 / 状态** | 实时展示状态、版本、PID、端口、数据目录与运行日志，支持**自动刷新**。 |

## 📦 安装插件

```bash
cd AstrBot/data/plugins
git clone https://github.com/icenfn/astrbot-plugin-cbm
```

随后在 WebUI → **插件管理** 中启用。

## 🚀 使用

1. WebUI → **插件管理** → **Codebase Memory MCP 管理器** → 进入插件详情页 → 打开 **「控制面板」**。
2. 面板顶部有 **概览 / 安装 / MCP / 调优 / 安全 / 日志** 六个标签页，按需切换。顶部工具条提供 **启动 / 停止 / 重启**（启动与停止按运行状态二选一显示）。
3. **安装** 页：选择版本类型（默认 **UI 版**）与版本号，如需可修改 **下载镜像**，点击 **安装**。
4. **概览** 页：在 **运行状态** 卡片查看状态 / 版本 / PID / 端口 / 数据目录 / **WebUI 地址**，并 **启动 / 停止 / 重启**。
5. UI 版启动后，**WebUI 地址** 会显示在运行状态卡片中，复制到浏览器打开即可（CBM 面板无法内嵌到当前页面）。
6. **MCP** 页会自动加载并刷新 CBM 的 stdio MCP 配置；点击 **添加到 AstrBot** 即可写入；也可 **复制** JSON 手动添加。重启 AstrBot 后生效。
7. **MCP** 页的 **Streamable-HTTP 端点** 卡片：勾选 **启用**，填写 **监听地址 / 端口 / 访问 Token**（必填，可点「生成并复制Token」自动生成并复制），点击 **保存并生效**。端点启动后，下方 **客户端模板** 会给出对应的 `mcpServers` 配置片段（含 `Authorization: Bearer <token>`），点击 **复制** 即可粘贴到客户端。客户端连接示例：
   ```json
   {
     "mcpServers": {
       "cbm": {
         "type": "streamable-http",
         "url": "http://127.0.0.1:8765/mcp",
         "headers": { "Authorization": "Bearer <token>" }
       }
     }
   }
   ```
8. **调优** 页：设置 CBM 运行时参数（留空表示自动/默认），保存后 **重启 CBM 生效**：
   - `budget_mb` → `CBM_MEM_BUDGET_MB`：内存图谱上限（MiB），超限写磁盘；
   - `physical_job_limit` → `CBM_WORKERS`：并行索引线程数（1–256）；
   - `CBM_RETAIN_TOTAL_MB` / `CBM_RETAIN_PER_FILE_MB`：索引阶段提取保留预算；
   - `CBM_MAX_FILE_BYTES`：跳过超大文件；`CBM_LOG_LEVEL`：日志级别。
9. **安全** 页：勾选 **启用访问鉴权** 并设置用户名 / 密码后点击 **保存并生效**；若面板正在运行，反向代理会自动重载使鉴权立即生效。
10. **日志** 页：查看运行日志，支持 **自动刷新**（每 5 秒）。

## ⚙️ 插件配置（`_conf_schema.json`）

| 配置项 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `variant` | string | `ui` | 默认版本类型：`ui` / `standard`。 |
| `version` | string | `latest` | 默认版本号：`latest` 或如 `0.11.0`。 |
| `mirror` | string | `https://gh-proxy.com/` | 下载加速镜像；留空直连。 |
| `cache_dir` | string | *(空)* | CBM 索引数据目录（`CBM_CACHE_DIR`）。 |
| `port` | int | `9749` | CBM UI 内部端口（仅本机）。 |
| `ui_proxy_host` | string | `0.0.0.0` | 可视化反向代理监听地址。 |
| `ui_proxy_port` | int | `9750` | 可视化反向代理对外端口（需与其不同并放行防火墙）。 |
| `ui_proxy_auth_enabled` | bool | `false` | 为可视化反向代理启用 HTTP Basic Auth。 |
| `ui_proxy_auth_username` | string | `admin` | 访问鉴权用户名。 |
| `ui_proxy_auth_password` | string | *(空)* | 访问鉴权密码；留空则鉴权不生效，建议在「安全」标签页中设置。 |
| `mcp_http_enabled` | bool | `false` | 是否启用 Streamable-HTTP MCP 端点（默认关闭）。 |
| `mcp_http_host` | string | `0.0.0.0` | Streamable-HTTP 端点监听地址。 |
| `mcp_http_port` | int | `8765` | Streamable-HTTP 端点端口（需放行防火墙）。 |
| `mcp_http_token` | string | *(空)* | Streamable-HTTP 端点访问 Token（**必填**，留空则端点不启动）。 |

> 运行时调优项（`CBM_MEM_BUDGET_MB`、`CBM_WORKERS` 等）不在插件配置项中，而在面板 **调优** 标签页设置，保存于插件数据目录 `runtime_env.json`，在 CBM 启动/MCP 接入时注入环境变量，重启后生效。
| `auto_start` | bool | `false` | 插件加载时自动启动 CBM。 |
| `stop_on_unload` | bool | `true` | 停用 / 卸载插件时自动停止 CBM。 |
| `purge_data_on_uninstall` | bool | `false` | 卸载 CBM 时是否一并删除索引数据。 |

> CBM 的面板仅监听 `127.0.0.1`，且会校验请求的 `Host` 与 `Origin`：只要不是回环地址就返回 `403`。本插件自带一个轻量反向代理，转发时会强制以 `127.0.0.1` 作为 `Host` 并去掉浏览器的 `Origin`，从而把面板安全暴露到 `ui_proxy_host:ui_proxy_port`，因此远程浏览器也能正常打开可视化。请确保该端口已放行防火墙。

## 🗂️ 目录结构

```
astrbot_plugin_cbm/
├── main.py              # AstrBot 集成层：Star + Web API
├── cbm_manager.py       # 核心逻辑：下载/校验/安装/运行/配置/反代（可独立测试）
├── metadata.yaml
├── _conf_schema.json
├── requirements.txt     # 无第三方依赖（仅标准库）
├── logo.png
└── views/dashboard/     # 插件 WebUI 页面（兼容旧目录 pages/）
    ├── index.html
    ├── style.css
    └── app.js
```

## 🧪 开发与测试

```bash
python -m pytest tests/ -v
```

## ⚠️ 说明

- 同一时间只管理一份 CBM 安装，切换版本类型需重新安装。
- CBM 由第三方项目维护，本插件仅负责下载、启动与配置编排。

## 📄 License

[MIT](./LICENSE)
