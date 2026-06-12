# MacroPruner-Ctx 环境搭建指南

## 系统要求

- Python **>= 3.10**（MCP SDK 要求）
- pip（Python 包管理器）
- 可选：venv（推荐）
- 可选：`clang`（如果要用 oracle backend；Ubuntu/Debian `apt install clang`，macOS `brew install llvm`）

## 安装步骤

### 1. 克隆项目

```bash
git clone https://github.com/shouchengcheng/macropruner-ctx.git
cd macropruner-ctx
```

### 2. 创建虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# Windows: .venv\Scripts\activate
```

### 3. 安装 MCP 依赖

```bash
pip install mcp
```

验证版本：

```bash
python3 -c "import mcp; print(mcp.__version__)"
# 预期输出: 1.x.x
```

### 4. 验证安装

跑单元测试套件：

```bash
.venv/bin/python test_pruner.py
.venv/bin/python test_pruner_realistic.py
.venv/bin/python test_expr_eval.py
.venv/bin/python test_skeletonizer.py
.venv/bin/python test_dep_graph.py
.venv/bin/python test_conditional_dep_graph.py
.venv/bin/python test_cc_parser_cache.py
.venv/bin/python test_config.py
.venv/bin/python test_errors.py
.venv/bin/python test_token_budget.py
.venv/bin/python test_clang_sysroot.py
.venv/bin/python test_patch_applier.py
.venv/bin/python test_cli.py
.venv/bin/python test_backends.py
.venv/bin/python test_mcp_server.py
```

所有 15 个套件应该打印 `All tests passed!` 或 `=== N/N passed ===`。

### 5. 快速启动 MCP server

```bash
source .venv/bin/activate
python3 mcp_server.py
```

MCP 服务器会在 stdio 上监听，等待 Agent（如 Hermes、Claude Desktop）连接。

## 常见问题

### Python 版本过低

MCP SDK 要求 Python >= 3.10。确认当前版本：

```bash
python3 --version
```

如果版本低于 3.10，请安装 Python 3.10 或更高版本。Ubuntu 20.04+ 自带 3.10+，macOS Homebrew 也能装。

### pip install mcp 报网络错误

使用国内镜像加速：

```bash
pip install mcp -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### .venv 中安装失败

确认虚拟环境已激活（终端提示符前有 `(.venv)` 标识），且 pip 版本较新：

```bash
pip install --upgrade pip
pip install mcp
```

### Linux 上 readline 报错（可选依赖）

MCP stdio 通信用 JSON-RPC 不需要 readline。如果出现 `ImportError: No module named 'readline'`：

```bash
# Debian / Ubuntu
sudo apt install libreadline-dev
# macOS — readline 自带
# 或者在 venv 装 pyreadline3
pip install pyreadline3
```

### macOS `libmagic` 报错

如果 Claude Desktop 集成时碰到 `libmagic` 缺失：

```bash
brew install libmagic
```

### Windows 上 `signal` 模块行为

Windows 不支持 `SIGINT` 等 POSIX 信号，MCP 进程在 Windows 上不能干净地 graceful shutdown。这是 MCP SDK 本身的限制，不是工具问题。

## 项目结构

```
macropruner-ctx/
├── cc_parser.py          # compile_commands.json 解析器
├── pruner_core.py        # 核心引擎：栈式状态机
├── expr_eval.py          # 完整的 #if 表达式求值器
├── skeletonizer.py       # Stage 2：函数体剥离
├── dep_graph.py          # Stage 3：#include 依赖图
├── token_counter.py      # LLM token 估算器
├── errors.py             # 错误分级 + 标签格式
├── patch_applier.py      # 独立 unified diff 应用器
├── config.py             # .macroprunerrc 解析
├── backends/             # 可插拔后端
│   ├── base.py           #   PrunerBackend ABC
│   ├── regex_backend.py  #   快速 pure-Python
│   └── clang_backend.py  #   ground-truth oracle
├── mcp_server.py         # MCP Server (stdio)
├── mcp_wrapper.sh        # Wrapper 脚本（Hermes 等 Agent 用）
├── cli.py                # 独立 CLI
├── test_*.py             # 15 个测试套件
├── test_samples/         # 测试样例 C 文件
├── integration/          # 真实 SDK 集成测试
│   ├── ws63_smoke.py
│   ├── ws63_smoke.log
│   └── ws63_integration_report.md
├── demo/                 # 端到端 demo
│   ├── demo.sh
│   └── README.md
├── docs/                 # 详细文档
│   ├── usage.md          #   操作手册
│   ├── CONFIG.md         #   .macroprunerrc 参考
│   ├── BACKENDS.md       #   backend 决策 + 跨编译
│   ├── ERRORS.md         #   错误协议
│   ├── ARCHITECTURE.md   #   内部架构
│   ├── CHANGELOG.md      #   版本历史
│   └── stage3-evaluation.md  #   Stage 3 历史评估
├── PLAN.md               # 架构 + 里程碑
├── README.md             # 项目首页
├── INTEGRATION.md        # 中文集成指南
├── SETUP.md              # 本文档
├── 小红书文案.md          # 营销文案
└── .zhiyu/               # 个人 plan 文件
```

## 卸载

```bash
deactivate        # 退出虚拟环境
cd .. && rm -rf macropruner-ctx
```

## 下一步

- 阅读 [README.md](README.md) 了解工具能做什么
- 阅读 [docs/usage.md](docs/usage.md) 学习详细用法
- 跟着 [INTEGRATION.md](INTEGRATION.md) 接入你的 Agent
- 跑 `bash demo/demo.sh` 看 demo
- 跑 `python3 integration/ws63_smoke.py` 看真实 SDK 表现（如果有 ws63 SDK）
