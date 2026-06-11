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
┌─────────────────────────────┐
│      mcp_server.py          │
│  ┌───────────────────────┐  │
│  │ Tool: read_c          │──│──→ 返回修剪后 C/C++ 源码
│  │   (file_path,         │  │
│  │    target,            │  │
│  │    compile_db,        │  │
│  │    mode)              │  │
│  └───────────────────────┘  │
└─────────────────────────────┘
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

## 第三步：在 Agent 中触发 read_c 调用

### Hermes：通过 SOUL.md

在 `~/.hermes/SOUL.md` 中添加：

```
当分析 C/C++ 源文件时，始终使用 read_c 工具而非直接读取文件。
传入 compile_db 参数指向项目的 compile_commands.json。

调用方式：
  read_c(file_path="src/main.c", target="PRODUCT_A",
         compile_db="/path/to/compile_commands.json")

target 参数传入当前产品/目标的宏名（如 "PRODUCT_A", "DEBUG"）。
compile_db 是必传参数。
```

### 通用设置

无论是哪个 Agent，核心原则相同：告诉 LLM 读取 C/C++ 文件时调用 `read_c` 而非普通读文件工具，并传入 `compile_db` 参数。

---

## read_c 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | C/C++ 源文件路径（绝对或相对路径） |
| `target` | string | 是 | 目标产品/宏名称，如 `"PRODUCT_A"`、`"DEBUG"` |
| `compile_db` | string | 是 | `compile_commands.json` 的路径 |
| `mode` | string | 否 | `"physical"`（默认）彻底删除；`"virtual"` 保留行号 |

### mode 对比

| mode | 行为 | 适用场景 |
|------|------|----------|
| `physical` | 彻底删除 inactive 代码块，最省 token | 常规 LLM 分析 |
| `virtual` | 替换为 `/* [IFDEF X - INACTIVE] */` 注释，保留行号 | 调试、需对齐行号 |

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