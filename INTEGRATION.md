# MacroPruner-Ctx 集成指南

## 概述

MacroPruner-Ctx 提供 MCP 服务器，让 LLM Agent（如 Hermes、Claude Desktop 等）在读取 C/C++ 源文件时自动修剪 inactive 的条件编译代码块，从而减少 token 消耗。

## 架构

```
LLM Agent
    │
    │ MCP 协议 (stdio)
    │
    ▼
┌──────────────────────────────────┐
│         mcp_server.py            │
│  ┌────────────────────────────┐  │
│  │ Tool: read_c               │──│→ 修剪后 C/C++ 源码
│  │ Tool: read_c_skeleton      │──│→ 骨架代码（仅签名+声明）
│  │ Tool: read_c_with_deps     │──│→ 多文件上下文（目标 prune + 依赖 skeleton）
│  │ Tool: apply_patch          │──│→ 将 unified diff 写回原文件
│  └────────────────────────────┘  │
└──────────────────────────────────┘
```

---

## 第一步：创建 Wrapper 脚本

Hermes 等 Agent 的 `--command` 参数将整串视为单一可执行文件路径，无法直接传参数。需要创建 wrapper 脚本：

```bash
#!/bin/bash
# mcp_wrapper.sh — 放在项目根目录
exec /path/to/.venv/bin/python3 /path/to/macropruner-ctx/mcp_server.py "$@"
```

赋予执行权限：

```bash
chmod +x mcp_wrapper.sh
```

> **说明：** MCP SDK 要求 Python >= 3.10。如果你的系统默认 python3 版本低于 3.10，请使用虚拟环境中 Python >= 3.10 的解释器。环境搭建详见 [SETUP.md](SETUP.md)。

---

## 第二步：注册 MCP 服务器

### Hermes Agent

```bash
hermes mcp add macropruner --command "/path/to/macropruner-ctx/mcp_wrapper.sh"
```

验证：

```bash
hermes mcp list
hermes mcp test macropruner
```

### Claude Desktop

在 `claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "macropruner": {
      "command": "/path/to/.venv/bin/python3",
      "args": ["/path/to/macropruner-ctx/mcp_server.py"]
    }
  }
}
```

---

## 第三步：配置 Agent（SOUL.md）

### Hermes：SOUL.md 配置

在 `~/.hermes/SOUL.md` 中添加以下内容。工具的具体参数、使用场景和约束已写入 MCP 工具的 description 字段，LLM 会通过 MCP 协议自动获取，无需在此重复。SOUL.md 只需规定全局行为约束：

```markdown
## C/C++ 代码分析工作流

读取任何 C/C++ 文件时，始终使用 MacroPruner-Ctx MCP 工具，不要直接读文件。

关键规则：
1. **每次调用都必须传 `compile_db`** — 指向项目的 compile_commands.json
2. **`target` 必须与代码中的 #ifdef 宏名一致**
3. 根据任务选择工具（工具 description 中有详细指南）：
   - 单文件分析 → `read_c`
   - 快速浏览接口 → `read_c_skeleton`
   - 跨文件依赖 → `read_c_with_deps`
   - 写回修改 → `apply_patch`（需先生成 unified diff）
```

### Claude Desktop

Claude Desktop 无需额外配置，只需在 `claude_desktop_config.json` 中注册 MCP 服务器（见第二步）。Claude 会自动发现可用工具并根据上下文选择调用。

---

## read_c 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | C/C++ 源文件路径（绝对或相对路径） |
| `target` | string | 是 | 目标产品/宏名称，如 `"PRODUCT_A"`、`"DEBUG"` |
| `compile_db` | string | 是 | `compile_commands.json` 的路径 |
| `mode` | string | 否 | `"physical"`（默认）彻底删除；`"virtual"` 保留行号 |
| `backend` | string | 否 | `"regex"`（默认）/`"clang"`/`"auto"` — 见 PLAN.md 后端章节 |

### mode 对比

| mode | 行为 | 适用场景 |
|------|------|----------|
| `physical` | 彻底删除 inactive 代码块，最省 token | 常规 LLM 分析 |
| `virtual` | 替换为 `/* [IFDEF X - INACTIVE] */` 注释，保留行号 | 调试、需对齐行号 |

---

## read_c_skeleton：获取代码结构骨架

当只需要了解代码结构（函数签名、struct/enum 定义、宏）而不需要具体实现时，使用 `read_c_skeleton`。它会先修剪条件编译块，再剥离所有函数体。

### 参数

与 `read_c` 完全相同（`file_path`, `target`, `compile_db`, `mode`）。

### 输出示例

```c
/* ── MacroPruner-Ctx (Skeleton) ─────────────── */
/* Target: PRODUCT_A                             */
/* Original: 200 lines                           */
/* Skeleton: 35 lines                            */
/* Functions stripped: 12                        */
/* ───────────────────────────────────────────── */

#include <stdio.h>
#define MAX_CONN 16

struct NetConfig {
    int port;
    char host[64];
};

int init_network(struct NetConfig *cfg);
void shutdown_network(void);
int send_data(const void *buf, size_t len);
```

### 适用场景

- 快速了解模块接口
- 生成 API 文档
- 跨模块依赖分析
- Token 极度紧张时的备选方案

---

## read_c_with_deps：多文件依赖上下文（Stage 3 Phase 1）

当 LLM 需要理解目标文件及其 `#include` 依赖的完整上下文时，使用 `read_c_with_deps`。它会解析 include 树，返回目标文件的完整修剪代码，以及依赖文件的骨架代码（仅签名），在单次调用中提供跨文件上下文。

### 参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `file_path` | string | 是 | — | C/C++ 源文件路径 |
| `target` | string | 是 | — | 目标产品/宏名称 |
| `compile_db` | string | 是 | — | `compile_commands.json` 路径 |
| `mode` | string | 否 | `"physical"` | `"physical"` 或 `"virtual"` |
| `max_depth` | int | 否 | `2` | 最大 include 深度 |

### 输出示例

```
/* ── MacroPruner-Ctx (with deps) ──────────────── */
/* Target: PRODUCT_A                                */
/* Root: app.c                                      */
/* Dependencies: 1 files                            */
/* Max depth: 2                                     */
/* Mode: physical                                   */
/* ─────────────────────────────────────────────── */

/* ══ TARGET FILE: app.c ══════════════════ */
/* Original: 20 lines | Pruned: 10 lines */

#include "utils.h"

void app_init(void) {
    device_info_t dev;
    init_device(&dev);
}

/* ══ DEPENDENCY: utils.h ══════════════════ */
/* Skeleton: 4 lines | Functions stripped: 0 */

#define UTILS_H
#include "types.h"
void log_message(const char *msg);
```

### 适用场景

- 分析跨文件函数调用链
- 理解 struct/enum 定义与使用的关系
- Token 预算紧张但仍需多文件上下文
- 调试头文件包含问题

---

## apply_patch：将 LLM 的修改写回原文件

LLM 看到修剪后的代码并给出修改建议后，通过 `apply_patch` 工具以 **unified diff** 格式写回原文件。这是最小改动的方案，不会覆盖未修改区域。

### 工作流程

```
用户: 把 init_network() 改成 return -1 if port == 0

LLM:
1. read_c("src/net.c", target="A") → 看到修剪后代码
2. 生成 unified diff:
   --- a/src/net.c
   +++ b/src/net.c
   @@ -45,6 +45,7 @@
    int init_network(int port) {
   +    if (port == 0) return -1;
        socket_fd = socket(AF_INET, ...);
3. apply_patch("src/net.c", diff)
```

### apply_patch 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | C/C++ 源文件路径 |
| `diff` | string | 是 | Unified diff 格式的补丁内容 |

---

## 典型会话示例

```
用户: 帮我分析 src/net/lwip_port.c 中的网络初始化逻辑

Agent 调用:
  read_c(file_path="src/net/lwip_port.c",
         target="PRODUCT_A",
         compile_db="/path/to/build/compile_commands.json")

→ 返回修剪后代码 + 压缩率统计
```

---

## 工作原理

1. Agent 启动时通过 stdio 启动 `mcp_server.py` 进程
2. MCP 协议交换工具列表，Agent 发现 `read_c` 工具
3. LLM 根据 system prompt 或用户请求决定调用 `read_c`
4. `read_c` 内部流程：
   - 解析 `compile_commands.json` 提取该文件的 `-D` 宏
   - 结合 target 宏构建活跃宏字典
   - 栈式状态机修剪 `#ifdef/#endif` 块
   - 返回修剪后代码 + 压缩率统计
5. 原始文件始终不变

---

## 故障排查

### read_c 工具未出现

确保 `/reset`（Hermes）或重启 Agent 会话以重新加载工具列表。

### Hermes 中报 "No such file or directory"

`--command` 将整串视为单个可执行文件路径，不要写成 `python3 /path/to/script.py`，应使用 wrapper 脚本。详见第一步。

### 修剪效果不符合预期

确认 `target` 名称与项目中 `#ifdef` 使用的宏名一致。可先试 `mode="virtual"` 查看哪些块被标记为 inactive。

---

## 命令速查（Hermes）

```bash
# 注册
hermes mcp add macropruner --command "/path/to/macropruner-ctx/mcp_wrapper.sh"

# 查看已注册的 MCP 服务器
hermes mcp list

# 测试连接
hermes mcp test macropruner

# 移除
hermes mcp remove macropruner

# 切换工具启用状态
hermes mcp configure macropruner

# 只读模式下重新加载 MCP（会话中）
/reload-mcp
```