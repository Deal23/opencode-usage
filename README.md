# OpenCode Usage

[![CI](https://github.com/Deal23/opencode-usage/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/Deal23/opencode-usage/actions/workflows/tests.yml)

轻量的 OpenCode 用量统计与跨设备汇总工具，支持 Windows、macOS 和 Linux，**零第三方运行时依赖**。

- 本地 HTML 看板、Markdown 报告和按需导出的 CSV 明细。
- 多设备快照合并、来源追踪和重复消息去重。
- 自动备份、同步状态与命令行自动化。

当前版本 **0.1.2**

**0.1.2 更新：**新增终端文档报告和模型参考价格查看；价格数据集中保存在工作目录的 `prices/`，支持按页面指引使用自己的 AI 维护。旧版顶层价格文件会在正常启动时迁移，冲突时不会覆盖。

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

选 **1 刷新**会更新统计、生成报告并把完整累计快照导出至工作目录的 `exports/`，不会打开浏览器；CSV 可在 **数据 → 导出 CSV** 中按需导出。完整快照包含已有会话标题和项目路径，只应交给可信设备。成功生成后仅保留最新一份可识别的程序导出；需长期保存请复制到其他目录。

在 **数据 → 模型参考价格** 可查看台账中所有设备和无来源记录的模型单价（人民币）与缺价状态，按供应商和 Token 用量排序；价格按最近消息日期展示，历史消息可能使用不同规则。
价格文件 `prices/model-prices.json` 保存在工作目录。打开 **数据 → 模型参考价格**，页面顶部会显示当前工作目录、价格文件和 AI 工作文档的路径；可将这些路径交给自己的 AI，按文档核实并更新。OCU 不自动调用 AI；联网检索前请注意隐私和费用。手动维护方法和价格来源优先级见[模型价格维护手册](docs/AUTOMATION.md#模型价格维护手册)。
工作目录可保存 Reverix 人民币基础价快照及阶梯，来源随供应商展示；该快照不改写已有消息费用。个人模型及价格配置不随源码分发。

主菜单 **v 可视化报告**打开 HTML；无图形 Linux 会显示文件路径，不会启动浏览器。**t 文档报告**可直接在终端快速浏览，适合 Linux / SSH。展示累计与今日/近 7/30 天用量、七日趋势、模型 Top 5、最近会话、设备来源和异常提醒。有 `less` 时分页（`q` 返回），否则直接显示（回车返回）；也可用 `cat ~/.local/share/ocusage/reports/report.txt` 查看默认目录下的看板。查看不采集或同步，数据以最近一次刷新快照为准。

在主菜单选择 **3 会话**，可进入、重命名或批量删除 OpenCode 会话。删除前会列出子会话并要求确认；已采集的用量保留，刷新后更新看板。

## 多设备汇总

- **手动：**各设备刷新后复制 `exports/` 中的快照，在汇总设备导入。
- **自动：**各设备使用独立工作目录，在 **数据 → 同步** 设置同一个云盘交换目录。OCU 交换快照，云盘负责传输。

## 数据与隐私

- 采集时只读源数据库；累计数据与报告默认保存在 `~/.local/share/ocusage/`。
- 刷新生成的本地快照包含完整信息；自动交换默认不包含标题和项目路径。
- 自动备份保护累计台账，也可备份整个工作目录。

## 文档与开发

- [第一次使用与常见问题](docs/QUICKSTART.md) · [安装与卸载](docs/INSTALLATION.md)
- [数据流、备份与恢复](docs/DATA_FLOW.md) · [自动化与报表设置](docs/AUTOMATION.md)
- [开发手册](docs/DEVELOPMENT.md) · [开发路线](docs/ROADMAP.md) · [MIT License](LICENSE)

```sh
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
```
