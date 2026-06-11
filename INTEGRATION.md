# MacroPruner-Ctx 集成指南

把 MacroPruner-Ctx 接入 LLM Agent 的完整步骤。

## 概述

MacroPruner-Ctx 是一个 MCP（Model Context Protocol）服务器，让 LLM Agent（Hermes、Claude Desktop 等）在读 C/C++ 源文件时自动剪掉 inactive 的 `#ifdef` / `#ifndef` / `#else` / `#elif` 代码块。

**核心能力：**
- 完整 `#if` 表达式求值（`#if MACRO == N`、`#if defined(A) && defined(B)`、Linux 风格 `IS_ENABLED`）
- 两级后端（regex 快 / clang 真值）
- 三层压缩（宏剪 → 骨架化 → 依赖图）
- 配置文件自动读取（`.macroprunerrc`）
- Token 节省统计

**4 个 MCP 工具：**
- `read_c` — 读单文件，剪 inactive 块
- `read_c_skeleton` — 剪 + 骨架化（剥函数体）
- `read_c_with_deps` — 多文件上下文（含条件 include 感知）
- `apply_patch` — 用 unified diff 写回原文件

## 第一步：环境准备

```bash
git clone https://github.com/shouchengcheng/macropruner-ctx.git
cd macropruner-ctx
python3 -m venv .venv
source .venv/bin/activate
pip install mcp
```

> MCP SDK 要求 Python >= 3.10。如果你的系统默认 python3 版本低于 3.10，请使用虚拟环境中 Python >= 3.10 的解释器。

跑测试确认环境：

```bash
.venv/bin/python test_pruner.py
.venv/bin/python test_backends.py
.venv/bin/python test_mcp_server.py
```

## 第二步：（推荐）写项目配置

在你**项目根**（不是 macropruner-ctx 目录）创建 `.macroprunerrc`：

```ini
# /path/to/your-firmware/.macroprunerrc

# 默认 target — MCP 调用省略时用这个
default_target = PRODUCT_3

# compile_commands.json 路径（相对项目根）
compile_db = build/compile_commands.json

# 默认后端：regex / clang / auto
default_backend = regex

# 默认模式：physical / virtual
default_mode = physical

# read_c_with_deps 的 include 遍历深度（1-5）
default_max_depth = 3
```

之后所有 MCP 调用都可以省略 `target` 和 `compile_db` 参数。

**配置查找顺序**（首个命中即用）：
1. MCP 调用参数（最高优先级）
2. 环境变量 `$MACROPRUNER_CONFIG`
3. `<项目根>/.macroprunerrc`（或 `macroprunerrc`）
4. `~/.macroprunerrc`
5. 内置默认值

## 第三步：启动 wrapper

Hermes 的 `--command` 参数把整串当成单一可执行文件路径。创建 wrapper 脚本：

`mcp_wrapper.sh`（项目根已有，路径指向 macropruner-ctx）：

```bash
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="${MACROPRUNER_VENV:-$SCRIPT_DIR/.venv/bin/python3}"
exec "$VENV_PYTHON" "$SCRIPT_DIR/mcp_server.py" "$@"
```

确保可执行：

```bash
chmod +x /path/to/macropruner-ctx/mcp_wrapper.sh
```

## 第四步：注册到 Agent

### Hermes Agent

```bash
hermes mcp add macropruner --command "/path/to/macropruner-ctx/mcp_wrapper.sh"

# 验证
hermes mcp list
hermes mcp test macropruner
```

### Claude Desktop

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "macropruner": {
      "command": "/path/to/macropruner-ctx/.venv/bin/python3",
      "args": ["/path/to/macropruner-ctx/mcp_server.py"]
    }
  }
}
```

重启 Claude Desktop。四个工具会自动出现在工具列表里。

## 第五步：使用

### 最小调用（用 .macroprunerrc 里的默认配置）

```
Agent: read_c(file_path="src/main.c")
```

输出：

```
/* --- MacroPruner-Ctx ---------------------------- */
/* Target:    PRODUCT_3                            */
/* Lines:     187/420 dropped (44.5%)              */
/* Tokens:    1230/2870 saved (42.9%)              */
/* Mode:      physical                             */
/* Backend:   regex                                */
/* ------------------------------------------------ */

void init_product3(void) { /* ... */ }
```

### 显式指定参数

```
read_c(
    file_path="src/wifi.c",
    target="PRODUCT_5",
    compile_db="/abs/path/to/compile_commands.json",
    mode="physical",
    backend="regex"
)
```

### 读多文件上下文

```
read_c_with_deps(
    file_path="src/wifi.c",
    target="PRODUCT_3",
    max_depth=3
)
```

返回 wifi.c（完整剪后） + 它 include 的所有头文件（剪后 + 骨架化）。**Phase 2 行为**：在 inactive `#if` 块内的 include **不会被跟随**。

### 写回修改

LLM 看完 `read_c` 输出后，生成 unified diff：

```
read_c(file_path="src/wifi.c")  → 看代码
generate diff                    → 准备改
apply_patch(file_path="src/wifi.c", diff="--- a/...")
                                 → 写回
```

## 工具参数详解

### read_c

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | C/C++ 源文件路径（绝对或相对） |
| `target` | string | 否 | 目标产品/宏名（缺省用 .macroprunerrc） |
| `compile_db` | string | 否 | compile_commands.json 路径（缺省用 .macroprunerrc / 自动发现） |
| `mode` | string | 否 | `"physical"` 彻底删 / `"virtual"` 保留行号 |
| `backend` | string | 否 | `"regex"`（默认） / `"clang"` / `"auto"` |

**`mode` 对比：**

| mode | 行为 | 场景 |
|------|------|------|
| `physical` | 删 inactive 块（最省 token） | 常规 LLM 阅读 |
| `virtual` | 替换为 `/* [INACTIVE] */` 注释，保留行号 | 调试、对齐原始行号 |

**`backend` 对比：**

| backend | 输出 | 速度 | 场景 |
|---------|------|------|------|
| `regex` | 原始 C 结构，宏保留 | 快 | 默认，LLM 阅读 |
| `clang` | 完整预处理（宏展开） | 慢 | 交叉验证 oracle |
| `auto` | 优先 clang，回退 regex | — | 一次性脚本 |

### read_c_skeleton

同 `read_c` 但额外剥函数体，只保留 struct/enum/typedef 定义和函数签名。比 read_c 再省 70-90% token。

### read_c_with_deps

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | 主 C/C++ 文件 |
| `target` | string | 否 | 缺省用 .macroprunerrc |
| `compile_db` | string | 否 | 缺省用 .macroprunerrc |
| `mode` | string | 否 | `"physical"` / `"virtual"` |
| `max_depth` | int | 否 | include 遍历深度（1-5，默认 2） |

### apply_patch

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | 要 patch 的 C/C++ 文件 |
| `diff` | string | 是 | unified diff 字符串 |

要求文件在 git 仓库里（用 `git apply --check` 验证）。非 git 仓库场景请手动 patch。

## 工作原理

1. Agent 启动时通过 stdio 启动 `mcp_server.py` 进程
2. MCP 协议交换工具列表，Agent 发现 `read_c` / `read_c_skeleton` / `read_c_with_deps` / `apply_patch`
3. LLM 根据上下文决定调用哪个
4. 后端处理流程：
   - 解析 `compile_commands.json` 提取该文件的 `-D` 宏（带 mtime 缓存）
   - 结合 `target` + 缓存中的宏列表
   - ExpressionEvaluator 求值 `#if` 表达式
   - PrunerCore 栈式状态机剪 `#ifdef/#endif` 块
   - 返回 PruneResult（code + skipped_ranges + token 节省）
5. 原始文件不变

## 故障排查

### 工具列表里没有 read_c

确保已重载 MCP（Hermes 用 `/reload-mcp`，Claude Desktop 重启）。

### "Cannot resolve file path"

`file_path` 相对当前工作目录。确保从项目根启动 Agent。

### "compile_commands.json not found"

- 确认 `.macroprunerrc` 里的 `compile_db` 路径正确（相对项目根）
- 或在每次调用里显式传 `compile_db` 绝对路径
- 或把 `compile_commands.json` 放到项目根或 `build/` 子目录

### 剪枝效果不符合预期

- 切到 `mode="virtual"` 看哪些块被标 `[INACTIVE]`
- 检查 `target` 名和 `#ifdef` 用的宏名是否一致
- 试 `backend="clang"` 拿 ground truth 对比

### 输出 token 数显示很怪

估算基于 `chars / 3.7`，对代码 ±15% 准确，老模型（GPT-3 davinci / claude-1）偏差大。

## 命令速查（Hermes）

```bash
# 注册
hermes mcp add macropruner --command "/path/to/macropruner-ctx/mcp_wrapper.sh"

# 查看
hermes mcp list

# 测试
hermes mcp test macropruner

# 移除
hermes mcp remove macropruner

# 切换工具启用状态
hermes mcp configure macropruner

# 会话中重载（只读）
/reload-mcp
```

## 完整使用手册

[docs/usage.md](docs/usage.md) 里有更详细的操作手册，包括：
- 完整 `#if` 语法支持表
- 各种工作流示例（审查 / 对比产品 / 审计 / 批量）
- 性能 / 缓存机制
- 完整的故障排查
