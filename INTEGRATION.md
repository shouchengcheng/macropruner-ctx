# MacroPruner-Ctx 集成指南

把 MacroPruner-Ctx 接入 LLM Agent 的完整步骤。

## 概述

MacroPruner-Ctx 是一个 MCP（Model Context Protocol）服务器，让 LLM Agent（Hermes、Claude Desktop 等）在读 C/C++ 源文件时自动剪掉 inactive 的 `#ifdef` / `#ifndef` / `#else` / `#elif` 代码块。

**核心能力**：
- 完整 `#if` 表达式求值（`#if MACRO == N`、`#if defined(A) && defined(B)`、Linux 风格 `IS_ENABLED`）
- 两级后端（regex 快 / clang 真值 oracle）
- 三层压缩（宏剪 → 骨架化 → 依赖图）
- 配置文件自动读取（`.macroprunerrc`）
- Token 节省统计
- Token budget 强制（超出自动降级 skeleton）
- 跨编译 SDK 支持（clang backend 加 `--sysroot` 跑 HiSilicon ws63、aarch64 等）
- 错误分级（`[FATAL]` / `[ERROR]` / `[WARN]`）
- 不依赖 git 的 apply_patch
- **`bootstrap_config` — LLM 自动生成 .macroprunerrc**（init-project 集成）

**5 个 MCP 工具**：
- `read_c` — 读单文件，剪 inactive 块
- `read_c_skeleton` — 剪 + 骨架化（剥函数体）
- `read_c_with_deps` — 多文件上下文（含条件 include 感知）
- `apply_patch` — 用 unified diff 写回原文件
- `bootstrap_config` — 自动生成 `.macroprunerrc`（init-project 集成；只在无配置时出现）

---

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

15 个套件应该全部打印 `All tests passed!` 或 `=== N/N passed ===`。

---

## 第二步：自动配置（init-project 集成）

如果你用 [init-project skill](https://github.com/) 管理项目（即项目根有 `PROJECT_MANIFEST.md`），`macropruner-ctx` 会在 MCP 启动时**自动检测**已有配置。

如果检测不到 `.macroprunerrc`，**5 个工具列表里会自动多一个 `bootstrap_config`**。LLM agent 第一次集成时会看到它。

### 典型工作流（LLM 自治）

```
User: 打开这个项目，帮我看看 uart.c 里 PRODUCT_3 的代码路径
LLM:  让我先看下有什么工具...
LLM:  哦，有个 bootstrap_config。先调一下看推荐什么。
LLM:  bootstrap_config()
      → 看到推荐：
        default_target = PRODUCT_3
        compile_db = ai/projects/ws63-app/compile_commands/cdb.json
        path_allowlist = [<project-root>]
      → 自动写：
        bootstrap_config(apply=True)
        → [OK] .macroprunerrc written
LLM:  read_c(file_path="src/uart.c")
      → 剪好的代码（target 自动取自 .macroprunerrc）
```

整个过程**用户没写一行配置**。

### bootstrap_config 怎么工作

**扫描优先级**（首个命中即用）：

1. **PROJECT_MANIFEST.md**（init-project 产物）
   - 解析 `active_project` 字段
   - 从 Project Matrix 拿当前项目的 `compile_commands` 路径
   - `target` 自动从 cdb 启发式推断
2. **compile_commands.json** 标准位置
   - `<project>/ai/projects/<project_id>/compile_commands/*.json`（init-project 标准）
   - `<project>/build/compile_commands.json`
   - `<project>/compile_commands.json`
   - 全项目 glob
3. **target 启发式**（从 cdb 的 `-D` 标志）：
   - `-DPRODUCT_TYPE=3` → `target=PRODUCT_3`（最常见）
   - `-DCHIP=WS63` → `target=CHIP_WS63`
   - 无 naming macro → `target=DEFAULT`

**写到哪**：`<project>/ai/projects/<active_project>/.macroprunerrc`
（init-project 标准的"项目级隔离"位置）

**安全**：
- 默认不覆盖现有文件（需 `force=True`）
- 生成的配置默认设 `path_allowlist = [<project_root>]`，**默认安全**
- LLM 显式 `apply=True` 才会写文件

### 何时不用 bootstrap_config

- **手写优先**的团队：直接写 `.macroprunerrc`，工具列表里就**不会出现** `bootstrap_config`
- **没有 PROJECT_MANIFEST.md** 的小项目：`bootstrap_config` 仍能工作（启发式扫描）
- **CI 自动化**：bootstrap 一次后 commit `.macroprunerrc` 到仓库，CI 读

### 切换项目时

如果项目有多个 product variant：

```ini
# .macroprunerrc 切 target
default_target = PRODUCT_5    # 改这一行即可
```

或运行时：

```
read_c(file_path="src/uart.c", target="PRODUCT_5")  # 覆盖 rc 的默认值
```

---

## 第二步（手动）：手写项目配置

如果你想完全手写，**项目根**（不是 macropruner-ctx 目录）创建 `.macroprunerrc`：

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

# ─── 跨编译 SDK 用户（HiSilicon ws63 / aarch64 等）───
# clang backend 要 oracle 真实预处理结果必须有 sysroot 路径。
# regex backend 不需要这个。
pruner.sysroot = <cross-sdk-sysroot>
pruner.extra_target = riscv32-linux-musl

# 可选：Token budget 强约束
token_budget = 0       # 0 = 不限制
```

之后所有 MCP 调用都可以省略 `target` 和 `compile_db` 参数。

**配置查找顺序**（首个命中即用）：
1. MCP 调用参数（最高优先级）
2. 环境变量 `$MACROPRUNER_CONFIG`
3. `<项目根>/.macroprunerrc`（或 `macroprunerrc`）
4. `~/.macroprunerrc`
5. 内置默认值

完整字段参考：`docs/CONFIG.md`。

---

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

---

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

### 通用 MCP 客户端

任何支持 stdio MCP 的客户端都行。指 `python3 mcp_server.py`（或 wrapper）即可。

---

## 第五步：使用

### 最小调用（用 .macroprunerrc 里的默认配置）

```
Agent: read_c(file_path="src/main.c")
```

输出：

```
/* --- MacroPruner-Ctx ---------------------------- */
/* Target:    PRODUCT_3                           */
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

### Token budget 强约束

```
read_c(
    file_path="src/big_file.c",
    token_budget=2000
)
```

pruned > 2000 token → 自动降级到 skeleton；连 skeleton 也超 → banner 标 `[WARN] Over budget`。详见 `docs/usage.md § 5.1`。

### 跨编译 SDK 模式

```
read_c(
    file_path="src/uart.c",
    backend="clang",         # 想用 clang oracle
    sysroot="<cross-sdk-sysroot>",
    extra_target="riscv32-linux-musl"
)
```

或者把这些写进 `.macroprunerrc`，MCP 调用就不用每次都传。

详见 `docs/BACKENDS.md`。

### 读多文件上下文

```
read_c_with_deps(
    file_path="src/wifi.c",
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

不需要 git 仓库——内置 applier 直接工作。

---

## 工具参数详解

### read_c

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | C/C++ 源文件路径（绝对或相对） |
| `target` | string | 否 | 目标产品/宏名（缺省用 .macroprunerrc） |
| `compile_db` | string | 否 | compile_commands.json 路径（缺省用 .macroprunerrc / 自动发现） |
| `mode` | string | 否 | `"physical"` 彻底删 / `"virtual"` 保留行号 |
| `backend` | string | 否 | `"regex"`（默认）/`"clang"`/`"auto"` |
| `token_budget` | int | 否 | 最大 token 数。0 = 无 cap。超出自动降级到 skeleton |
| `sysroot` | string | 否 | Clang-only：跨编译 SDK 的 sysroot 路径 |
| `extra_target` | string | 否 | Clang-only：`--target=` 值（如 `riscv32-linux-musl`） |

**`mode` 对比**：

| mode | 行为 | 场景 |
|------|------|------|
| `physical` | 删 inactive 块（最省 token） | 常规 LLM 分析 |
| `virtual` | 替换为 `/* [INACTIVE] */` 注释，保留行号 | 调试、对齐原始行号 |

**`backend` 对比**：

| backend | 输出 | 速度 | 场景 |
|---------|------|------|------|
| `regex` | 原始 C 结构，宏保留 | 快 | 默认，LLM 阅读 |
| `clang` | 完整预处理（宏展开） | 慢 | 交叉验证 oracle（`sysroot` 必须对） |
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

**Backend 行为**：
- 在 git 仓库里 → 优先 `git apply --check` + `git apply`（最可靠）
- 不在 git → 纯 Python 内置 applier
- 应用后做 syntax check：括号配平 + #if/#endif 配对
- 全部通过 → `[OK] ...`；结构异常 → `[OK] ... [WARN] Syntax check found ...`

---

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

完整数据流：`docs/ARCHITECTURE.md`。

---

## 跨编译 SDK 用户场景

HiSilicon WS63、aarch64 这类 cross-compile SDK 用户的额外步骤：

```bash
# 1. 确认 SDK 装在哪（典型路径）
ls <cross-sdk-sysroot>/

# 2. 写进 .macroprunerrc
cat >> .macroprunerrc <<'EOF'
pruner.sysroot = <cross-sdk-sysroot>
pruner.extra_target = riscv32-linux-musl
EOF

# 3. 现在 clang backend 可以 oracle
hermes mcp test macropruner
# LLM agent:
#   read_c(file_path="src/uart.c", backend="clang")
#   正常返回（之前会 [FATAL] 因为找不到 nv_porting.h 等 SDK 头文件）
```

详细：`docs/BACKENDS.md`。

---

## 故障排查

### 工具列表里没有 read_c

确保已重载 MCP（Hermes 用 `/reload-mcp`，Claude Desktop 重启）。

### "Cannot resolve file path"

`file_path` 相对当前工作目录。确保从项目根启动 Agent。

### "compile_commands.json not found"

- 确认 `.macroprunerrc` 里的 `compile_db` 路径正确（相对项目根）
- 或在每次调用里显式传 `compile_db` 绝对路径
- 或把 `compile_commands.json` 放到项目根或 `build/` 子目录

### "no clang binary found on PATH"

```bash
# Ubuntu / Debian
sudo apt install clang
```

或者只用 `backend="regex"` / `backend="auto"`（自动回退 regex）。

### 跨编译 SDK 上 clang 失败

设 `--sysroot`（参考上面"跨编译 SDK 用户场景"）。

### 剪枝效果不符合预期

- 切到 `mode="virtual"` 看哪些块被标 `[INACTIVE]`
- 检查 `target` 名和 `#ifdef` 用的宏名是否一致
- 试 `backend="clang"` 拿 ground truth 对比

### 输出 token 数显示很怪

估算基于 `chars / 3.7`，对代码 ±15% 准确，老模型（GPT-3 davinci / claude-1）偏差大。

### apply_patch 报 "context mismatch"

diff 的 `@@ -N,M @@` 偏移已经和当前文件不匹配。重新从当前文件内容生成 diff。

### 错误字符串识别

工具返回字符串以 `[FATAL]` / `[ERROR]` / `[WARN]` 开头，LLM 应当 grep 识别。详见 `docs/ERRORS.md`。

### Token 超额不降级

`token_budget` 必须是正整数。0 关闭。`docs/usage.md § 5.1` 详解。

---

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

---

## 不接 MCP 也能用：CLI 模式

```bash
# 剪一个文件
.venv/bin/python cli.py read src/main.c --target PRODUCT_3 --cdb build/compile_commands.json

# 骨架化
.venv/bin/python cli.py skeleton src/main.c --target PRODUCT_3

# diff 模式（regex vs clang oracle）
.venv/bin/python cli.py diff src/main.c --target PRODUCT_3

# 跨编译 SDK
.venv/bin/python cli.py read src/uart.c --backend clang \
    --sysroot <cross-sdk-sysroot> --target-arg riscv32-linux-musl \
    --cdb output/ws63/acore/ws63-liteos-app/compile_commands.json
```

完整 CLI 参考：`docs/usage.md § 12`。

---

## 完整使用手册

[docs/usage.md](docs/usage.md) 里有更详细的操作手册：
- 概念、安装、.macroprunerrc、MCP 集成、4 个工具详解、backend 选择、#if 语法表
- 4 个工作流示例（审查 / 对比产品 / 审计 / 批量）
- 性能 / 缓存机制
- 完整的故障排查
- reference
