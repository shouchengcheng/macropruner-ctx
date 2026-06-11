# MacroPruner-Ctx 环境搭建指南

## 系统要求

- Python >= 3.10
- pip（Python 包管理器）
- 可选：venv（Python 虚拟环境，推荐使用）

## 安装步骤

### 1. 克隆项目

```bash
git clone <repo-url> macropruner-ctx
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

安装后验证版本：

```bash
python3 -c "import mcp; print(mcp.__version__)"
# 预期输出: 1.x.x
```

### 4. 验证安装

运行单元测试：

```bash
python3 test_pruner.py
```

预期输出（7 个测试全部通过）：

```
TEST simple_ifdef: PASS
TEST nested_ifdef_deep: PASS
TEST ifndef: PASS
TEST else_toggle: PASS
TEST elif_chain: PASS
TEST physical_deletion: PASS
TEST physical_with_else: PASS
```

运行 E2E 测试：

```bash
python3 test_mcp_server.py
```

预期输出（4 个测试全部通过）：

```
TEST list_tools: PASS
TEST read_c: PASS
TEST read_c (virtual mode): PASS
TEST read_c (with explicit compile_db): PASS

All MCP server tests passed!
```

## 快速启动

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

如果版本低于 3.10，请安装 Python 3.10 或更高版本。

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

## 项目结构

```
macropruner-ctx/
├── cc_parser.py          # compile_commands.json 解析器
├── pruner_core.py        # 核心引擎：栈式状态机处理 #ifdef 嵌套
├── mcp_server.py         # MCP Server，通过 stdio 暴露 read_c 工具
├── mcp_wrapper.sh        # Wrapper 脚本（Hermes 等 Agent 使用）
├── test_pruner.py        # 单元测试（7 个用例）
├── test_mcp_server.py    # E2E 测试（4 个用例）
├── test_samples/         # 测试样例 C 文件
├── PLAN.md               # 架构文档
├── SETUP.md              # 本文档
└── INTEGRATION.md        # Agent 集成指南
```