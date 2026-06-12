# MacroPruner-Ctx：给 LLM 看 C/C++ 代码，Token 节省高达 87%

---

做嵌入式或 C/C++ 开发的痛点：把代码丢给 LLM，它先啃几百行 `#ifdef` 兼容分支，Token 烧光了还没进入正题。

**MacroPruner-Ctx** 专为解决这个问题而生：**在 LLM 读取代码前，自动剔除不活跃的 `#ifdef` 分支。**

---

## 核心功能

### 1. 智能宏裁剪 (Macro Pruning)
- 完整支持 `#ifdef` / `#ifndef` / `#else` / `#elif` / `#if` 复杂表达式（含 `defined()`、`&&`/`||`/`!`、算术比较、hex）
- 自动从 `compile_commands.json` 提取编译宏，无需手动配置
- **单文件压缩率 40%-60%**，有效代码量翻倍

### 2. 骨架化浏览 (Skeletonization)
- 剥离函数体，仅保留 struct/enum/typedef、宏定义和函数签名
- 适合快速掌握模块接口，**再省 70-90% Token**

### 3. 跨文件依赖图 (Dependency Graph)
- 解析 `#include` 树，条件感知遍历（不拉取非目标分支的头文件）
- 目标文件完整裁剪 + 依赖文件骨架化，LLM 获得完整模块视角

### 4. 精准补丁应用 (Apply Patch)
- LLM 生成 unified diff，工具精准写回原文件
- 内置语法检查（括号平衡、条件编译匹配），最小化改动风险

---

## 技术亮点

- **双后端架构**：regex（极速，默认）+ clang（地面真理，支持交叉编译 SDK）
- **Token 预算控制**：超限自动降级为骨架化，确保不超预算
- **配置驱动**：`.macroprunerrc` 统一管理默认目标、编译数据库路径
- **编译数据库缓存**：mtime 感知，16 条目 LRU 缓存，二次调用 <20ms
- **错误标签协议**：`[FATAL]` / `[ERROR]` / `[WARN]` 结构化输出，便于 LLM 解析

---

## 四个 MCP 工具

| 工具 | 用途 |
|------|------|
| `read_c` | 单文件宏裁剪（默认入口） |
| `read_c_skeleton` | 骨架化浏览（只看接口） |
| `read_c_with_deps` | 多文件上下文（目标全量 + 依赖骨架） |
| `apply_patch` | 应用 unified diff 到原文件 |

一行命令接入 Hermes、Claude Desktop 等任意 MCP 客户端。

---

## 快速开始

```bash
git clone https://github.com/shouchengcheng/macropruner-ctx.git
cd macropruner-ctx
python3 -m venv .venv && source .venv/bin/activate
pip install mcp

# 在项目根目录创建配置
cat > .macroprunerrc <<'EOF'
default_target = PRODUCT_A
compile_db      = build/compile_commands.json
EOF

# 注册到 Hermes
hermes mcp add macropruner --command "/path/to/mcp_wrapper.sh"
```

Agent 调用示例：
```
read_c(file_path="src/main.c")  # 自动从配置读取 target 和 compile_db
```

---

## 实测数据

在真实 cross-compile 固件 SDK（riscv32 交叉编译，120+ 宏/文件）上的表现：

| 场景 | 行数减少 | Token 节省 | 耗时 |
|------|---------|-----------|------|
| 单文件 `read_c` | 20-36% | 7-26% | 0.2s |
| `read_c_skeleton` | 89% | ~80% | 0.1s |
| 缓存命中后 | — | — | <0.05s |

**整体 Token 节省范围：7% – 87%**，取决于文件中非活跃分支的比例。

---

## 适合人群

- 嵌入式开发 / 多产品线维护，`#ifdef` 满天飞
- 用 LLM 做代码审查、重构、文档生成
- 希望将 LLM 深度集成到 C/C++ 工作流

---

**开源地址：** [github.com/shouchengcheng/macropruner-ctx](https://github.com/shouchengcheng/macropruner-ctx)

**一句话总结：** 让 LLM 只看它该看的代码，别在条件编译的垃圾堆里翻东西。

#程序员 #嵌入式开发 #AI编程 #开源工具 #效率工具 #C语言 #MCP