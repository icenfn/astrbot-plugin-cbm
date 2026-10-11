# astrbot_plugin_cbm

> 在 AstrBot WebUI 中一站式管理 **Codebase Memory MCP**（CBM）—— 面向 AI 编码代理的代码记忆 / 知识图谱引擎。

把 [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) 的安装、运行与配置搬进 AstrBot 插件面板，无需登录服务器敲命令。

## ✨ 功能

| 功能 | 说明 |
| --- | --- |
| **安装 / 卸载** | 可选 **UI 版**（内置 3D 可视化面板，**默认**）或 **标准版**（无界面 MCP 服务）；版本号可下拉选择；自动下载并强制校验 **SHA-256**。未安装显示「安装」，已安装显示「重新安装 / 更新」与「卸载」。 |
| **下载镜像** | 默认 `https://gh-proxy.com/` 加速下载，可自定义；留空则直连 GitHub。 |
| **运行控制** | 一键 **启动 / 停止 / 重启**，实时查看状态。 |
| **可视化访问** | 内置反向代理，把 CBM 面板对外暴露，远程浏览器也能打开 3D 图谱。 |
| **配置管理** | 图形化读写 CBM 的 `config`，支持单项重置。 |
| **日志 / 状态** | 实时展示状态、版本、PID、端口、数据目录与运行日志。 |

## 📦 安装插件

```bash
cd AstrBot/data/plugins
git clone https://github.com/icenfn/astrbot-plugin-cbm
```

随后在 WebUI → **插件管理** 中启用。

## 🚀 使用

1. WebUI → **插件管理** → **Codebase Memory MCP 管理器** → 进入插件详情页。
2. 打开 **「控制面板」**。
3. 在 **安装 / 卸载** 卡片选择版本类型（默认 **UI 版**）与版本号，如需可修改 **下载镜像**，点击 **安装**。
4. 安装完成后，在 **运行状态** 卡片点击 **启动**。
5. UI 版启动后，点击 **打开可视化 ↗**，或复制面板中显示的可视化地址在新标签页打开。

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
| `auto_start` | bool | `false` | 插件加载时自动启动 CBM。 |
| `stop_on_unload` | bool | `true` | 停用 / 卸载插件时自动停止 CBM。 |
| `purge_data_on_uninstall` | bool | `false` | 卸载 CBM 时是否一并删除索引数据。 |

> CBM 的面板仅监听 `127.0.0.1` 且会校验 Host 头，远程无法直连。本插件自带一个轻量反向代理，把面板暴露到 `ui_proxy_host:ui_proxy_port`，因此远程浏览器也能打开可视化。请确保该端口已放行防火墙。

## 🗂️ 目录结构

```
astrbot_plugin_cbm/
├── main.py              # AstrBot 集成层：Star + Web API
├── cbm_manager.py       # 核心逻辑：下载/校验/安装/运行/配置/反代（可独立测试）
├── metadata.yaml
├── _conf_schema.json
├── requirements.txt     # 无第三方依赖（仅标准库）
├── logo.png
└── pages/dashboard/     # 插件 WebUI 页面
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
