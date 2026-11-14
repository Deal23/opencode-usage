# OpenCode Usage

[![CI](https://github.com/Deal23/opencode-usage/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/Deal23/opencode-usage/actions/workflows/tests.yml)

轻量的 OpenCode 用量统计与跨设备汇总工具，支持 Windows、macOS 和 Linux，**零第三方运行时依赖**。

- 本地 HTML 看板、Markdown 报告和按需导出的 CSV 明细。
- 多设备快照合并、来源追踪和重复消息去重。
- 自动备份、同步状态与命令行自动化。

当前版本 **0.1.0**

## 看板预览

![OpenCode Usage 看板示例](docs/images/dashboard.svg)

查看用量趋势、模型排行、项目与设备分布、同步状态和会话明细。点击图片可查看大图。

## 安装

需要 Python 3.9+。通过 Git 下载源码：

```sh
git clone https://github.com/Deal23/opencode-usage.git
cd opencode-usage
```

或在仓库页面选择 **Code → Download ZIP**，解压后进入源码目录。

运行安装命令：

```sh
python3 install.py       # macOS / Linux
py -3 install.py         # Windows
```

按提示刷新终端后运行 `ocu`。Linux 缺少 venv 时需安装 `python3-venv`。更多信息见[安装、更新与卸载](docs/INSTALLATION.md)。

## 终端面板

首次运行设置设备名称，通过菜单刷新报告、管理数据或调整设置：

![OCU 终端操作面板](docs/images/terminal.svg)

刷新生成 HTML 和 Markdown；CSV 可在 **数据 → CSV** 中按需导出。

## 多设备汇总

- **手动：**各设备刷新并导出快照，在汇总设备导入。
- **自动：**各设备使用独立工作目录，在 **数据 → 同步** 设置同一个云盘交换目录。OCU 交换快照，云盘负责传输。

各设备维护独立台账，通过快照汇总用量历史。

## 数据与隐私

- 源数据库只读；累计数据与报告默认保存在 `~/.local/share/ocusage/`。
- 手动导出支持完整信息或“仅用量”；自动交换默认不包含标题和项目路径。
- 自动备份保护累计台账，也可备份整个工作目录。

## 文档与开发

- [第一次使用与常见问题](docs/QUICKSTART.md) · [安装与卸载](docs/INSTALLATION.md)
- [数据流、备份与恢复](docs/DATA_FLOW.md) · [自动化与报表设置](docs/AUTOMATION.md)
- [开发手册](docs/DEVELOPMENT.md) · [开发路线](docs/ROADMAP.md) · [MIT License](LICENSE)

```sh
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
```
