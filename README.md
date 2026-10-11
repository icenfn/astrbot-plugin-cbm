# astrbot_plugin_cbm

> 在 AstrBot WebUI 中一站式管理 **Codebase Memory MCP**（CBM）—— 面向 AI 编码代理的代码记忆 / 知识图谱引擎。

本插件把 [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) 的安装、运行与配置全部搬进 AstrBot 的插件面板，无需登录服务器敲命令。

<br/>

## ✨ 功能

| 功能 | 说明 |
| --- | --- |
| **安装 / 卸载** | 可选 **UI 版**（内置 3D 知识图谱可视化面板，**默认**）或 **标准版**（无界面 MCP 服务）；**版本号可从下拉列表选择**（含 latest 与历史版本）；自动从官方 Release 下载并强制校验 **SHA-256**。 |
| **下载镜像** | 默认使用 `https://gh-proxy.com/` 加速下载，可在配置或面板中自定义；留空则直连 GitHub。 |
| **运行控制** | 一键 **启动 / 停止 / 重启** CBM 服务进程。 |
| **配置管理** | 图形化读写 CBM 的 `config`（`auto_index`、`auto_index_limit`、`auto_watch`、`watcher_enabled`、`ui_enabled`、`ui_port`、`ui-lang` 等），支持单项重置。 |
| **状态显示** | 实时展示运行状态、版本、PID、端口、运行时长、数据目录与运行日志。 |

所有操作都在插件详情页的 **「控制面板」** 页面完成，采用卡片式布局与即时反馈，简洁直观。

<br/>

## 📦 安装插件

### 方式一：插件市场
在 AstrBot WebUI → **插件市场** 搜索 `Codebase Memory MCP` 安装。

### 方式二：手动
```bash
cd AstrBot/data/plugins
git clone https://github.com/icenfn/astrbot-plugin-cbm
```
随后在 WebUI → **插件管理** 中启用。

<br/>

## 🚀 使用

1. 打开 WebUI → **插件管理** → 找到 **Codebase Memory MCP 管理器** → 点击进入插件详情页。
2. 打开 **「控制面板」** 页面。
3. 在 **安装 / 卸载** 卡片中选择版本类型（默认 **UI 版**）与版本号（下拉选择），如需可修改 **下载镜像**，点击 **安装 / 更新**。
4. 安装完成后，在 **运行状态** 卡片点击 **启动**。
5. 安装 **UI 版** 并启动后，点击 **打开可视化 ↗** 即可打开 3D 图谱面板。

<br/>

## ⚙️ 插件配置（`_conf_schema.json`）

| 配置项 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `variant` | string | `ui` | 默认版本类型：`ui` / `standard`。 |
| `version` | string | `latest` | 默认版本号，填 `latest` 使用最新稳定版，或指定如 `0.10.8`。 |
| `mirror` | string | `https://gh-proxy.com/` | 下载加速镜像，默认 `https://gh-proxy.com/`；留空直连 GitHub。支持 `{url}` 占位符，否则前缀拼接。 |
| `cache_dir` | string | *(空)* | CBM 索引数据目录（`CBM_CACHE_DIR`）。留空使用 `~/.cache/codebase-memory-mcp`。 |
| `port` | int | `9749` | UI 版 HTTP 服务端口。 |
| `auto_start` | bool | `false` | 插件加载时自动启动 CBM。 |
| `stop_on_unload` | bool | `true` | 卸载 / 停用插件时自动停止 CBM。 |
| `purge_data_on_uninstall` | bool | `false` | 卸载 CBM 时是否一并删除索引数据。 |

<br/>

## 🗂️ 目录结构

```
astrbot_plugin_cbm/
├── main.py              # AstrBot 集成层：Star + Web API（register_web_api）
├── cbm_manager.py       # 核心逻辑：下载/校验/安装/运行/配置（可独立测试）
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # 配置项定义
├── requirements.txt     # 无第三方依赖（仅标准库）
├── logo.png             # 插件图标 (256x256)
└── pages/
    └── dashboard/       # 插件 WebUI 页面
        ├── index.html
        ├── style.css
        └── app.js
```

插件运行数据（二进制、状态、日志、下载缓存）存放在 AstrBot 的插件数据目录：

```
data/plugin_data/astrbot_plugin_cbm/
├── bin/codebase-memory-mcp   # 下载的可执行文件
├── downloads/                # 下载缓存
├── logs/cbm.log              # 运行日志
├── state.json                # 运行状态
└── install.json              # 安装信息
```

<br/>

## 🔌 Web API

页面通过 `window.AstrBotPluginPage` bridge 调用以下后端接口（路由前缀 `/{plugin_name}` 由 AstrBot 转发）：

| 方法 | Endpoint | 说明 |
| --- | --- | --- |
| GET | `status` | 运行状态 |
| GET | `versions` | 已安装 / 最新版本 |
| POST | `install` | 开始安装 `{variant, version, mirror?}` |
| GET | `install/progress` | 安装进度 |
| POST | `uninstall` | 卸载 `{purge_data?}` |
| POST | `start` / `stop` / `restart` | 运行控制 |
| GET / POST | `config` | 读取 / 修改配置 |
| POST | `config/reset` | 重置配置项 |
| GET | `logs` | 读取运行日志 |

<br/>

## 🧪 开发与测试

核心逻辑 `cbm_manager.py` 不依赖 AstrBot，可独立测试：

```bash
python -m pytest tests/ -v
```

<br/>

## ⚠️ 说明

- 本插件不开发多版本共存：同一时间只管理一份 CBM 安装，切换版本类型需重新安装。
- UI 版与标准版来自官方 Release 的不同资产，UI 版内置可视化 HTTP 服务。
- CBM 由第三方项目维护，本插件仅负责下载、启动与配置编排。

<br/>

## 📄 License

[MIT](./LICENSE)
