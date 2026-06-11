# MacroPruner-Ctx

编译条件代码修剪工具，用于 LLM 读取 C/C++ 文件时自动移除 inactive 的 `#ifdef` / `#ifndef` 代码块，大幅减少 token 消耗。

## 架构

```
LLM Agent (Hermes, Claude Desktop, ...)
    │
    │ MCP 协议 (stdio)
    │
    ▼
┌─────────────────────────────┐
│      mcp_server.py          │
│  ┌───────────────────────┐  │
│  │ Tool: read_c          │──│→ 返回修剪后 C/C++ 源码
│  │   (file_path, target, │  │     + 压缩率统计
│  │    compile_db, mode)  │  │
│  └───────────────────────┘  │
└─────────────────────────────┘
```

## 快速开始

```bash
git clone <repo-url> && cd macropruner-ctx
python3 -m venv .venv && source .venv/bin/activate
pip install mcp
python3 test_pruner.py
```

详细环境搭建见 [SETUP.md](SETUP.md)。

Agent 集成指南见 [INTEGRATION.md](INTEGRATION.md)。

## 核心功能

| 指令 | 支持 |
|------|------|
| `#ifdef` / `#ifndef` | 正确评估嵌套状态 |
| `#else` | 切换当前层 active/inactive |
| `#elif` 链 | 首个匹配分支激活 |
| `#if` (简单宏名) | 宏存在 → active |

**两种输出模式：**
- `physical`：彻底删除 inactive 块（最省 token）
- `virtual`：替换为注释标记，保留行号（适合调试）

## 项目结构

```
macropruner-ctx/
├── cc_parser.py          # compile_commands.json 解析器
├── pruner_core.py        # 核心引擎：栈式状态机
├── mcp_server.py         # MCP Server (stdio)
├── mcp_wrapper.sh        # Wrapper 脚本
├── test_pruner.py        # 单元测试（7 个用例）
├── test_mcp_server.py    # E2E 测试（4 个用例）
├── test_samples/         # 测试样例
├── PLAN.md               # 架构文档
├── SETUP.md              # 环境搭建指南
└── INTEGRATION.md        # Agent 集成指南
```