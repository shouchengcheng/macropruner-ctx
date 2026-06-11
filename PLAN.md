# Role & Context
You are an expert Principal Software Engineer specializing in C/C++ compiler toolchains, AST parsing, and LLM orchestration workflows. You are tasked with developing a high-performance productivity tool named **`MacroPruner-Ctx`**.

### The Core Pain Point
In large-scale, multi-firmware C/C++ embedded or Linux systems, a single repository often generates multiple target products. Developers heavily rely on conditional compilation (`#ifdef`, `#ifndef`, `#else`, `#endif`) and preprocessor macros to isolate product-specific code. 
When feeding code files into LLM prompts for analysis, standard AI tools are "macro-blind"—they inject the entire file, including thousands of lines of inactive, uncompiled code from other products. This leads to massive token waste, contextual noise, and severe LLM hallucinations.

### Project Vision
`MacroPruner-Ctx` acts as a **"Macro-Aware Context Pruner"** between a complex C/C++ repository and an LLM. It mimics a compiler's preprocessor to strip out inactive code, extract structural skeletons, and generate optimized, highly-concentrated context prompts for LLMs.

---

# 🏗️ System Architecture & Core Modules

You must maintain and develop the tool across these four discrete functional modules:

1. **Compile DB Parser (`cc_parser.py`)**
   - **Input:** Target source file path (e.g., `src/net/lwip_port.c`) and the project's `compile_commands.json`.
   - **Logic:** Locate the matching file entry. Safely tokenize the `command` or `arguments` array (handling spaces/escapes). Use regex to filter out all global macros defined via `-D` flags (e.g., `-DPRODUCT_TYPE=3`, `-DUSE_LWIP`).
   - **Output:** A dictionary/list of active macros for that specific file.

2. **Conditional Compilation Pruner (`pruner_core.py`)**
   - **Input:** Raw source code text, list of active macros.
   - **Logic:** A robust line-by-line state machine utilizing a stack to handle deeply nested conditional blocks (`#ifdef`, `#ifndef`, `#elif`, `#else`, `#endif`).
   - **Pruning Strategies (Configurable):**
     - *Physical Deletion:* Drop inactive code blocks entirely to minimize tokens.
     - *Virtual Folding:* Replace inactive blocks with `/* [Skipped for TARGET_PROD] */` and pad with empty lines to preserve original file line numbers for accurate debugging.

3. **Context Aggregator (`aggregator.py`)**
   - **Logic:** Reads a local `PROJECT_MANIFEST.md` containing global rules, system architecture, and coding guidelines. Combines the [Global Rules] + [Active Macros Info] + [Pruned Clean Source Code] into a single structured Markdown Prompt.
   - **Token Guard:** Integrates a token counter. If the aggregate prompt exceeds a set budget (e.g., 10k tokens), triggers secondary pruning (e.g., stripping non-essential comments or enabling skeleton mode).

4. **Interface & Delivery Engine (`cli.py` / `main.py`)**
   - **CLI Mode:** Command-line tool supporting flags like `macropruner ./main.c --copy` (autocopy pruned text to clipboard).
   - **LLM Direct Mode:** Optional integration with LLM APIs (e.g., Gemini API) allowing inline queries like: `macropruner ./main.c -q "Explain this timeout logic"`.

---

# 🚀 Engineering Roadmap & Multi-File Escalation Strategy

To scale this tool for massive codebases, we implement a **Four-Stage Code Reduction Funnel**:
- **Stage 1 (Macro Pruning):** Drop inactive conditional code (Reduces size by 30-60%).
- **Stage 2 (Skeletonization):** Strip function bodies `{ ... }` using lightweight AST tokens or regex, keeping only `struct` definitions, `#defines`, and function signatures when full code isn't required.
- **Stage 3 (Dependency Graphing):** Parse `#include` trees and symbols to pull target files as full-text (pruned) while pulling immediate dependencies as structural skeletons only.
- **Stage 4 (Token Budgeting):** Rigid cap enforcement prior to LLM dispatch.

---

# ✅ Current Project Status — Milestone 1: Complete

All four core modules have been implemented and production-tested:

### Implemented Modules
| Module | File | Status | Tests |
|--------|------|--------|-------|
| **Compile DB Parser** | `cc_parser.py` | ✅ Complete | — |
| **Conditional Pruner** | `pruner_core.py` | ✅ Complete | 7/7 PASS |
| **MCP Server** | `mcp_server.py` | ✅ Complete | 3/3 PASS (E2E) |
| **Test Suite** | `test_pruner.py` / `test_mcp_server.py` | ✅ Complete | — |

### MCP Integration Architecture
```
LLM Client (Anthropic, Claude Desktop, etc.)
        │
        │  MCP Protocol (stdio)
        ▼
┌─────────────────────────────┐
│      mcp_server.py          │
│  ┌───────────────────────┐  │
│  │ Tool: read_c          │──│──→ pruner_core.prune() → pruned source
│  │   (file_path, target, │  │
│  │    mode)              │  │
│  │                       │  │
│  │ Resource: cfile://    │  │── raw file content (no pruning)
│  └───────────────────────┘  │
│  Uses: cc_parser for -D     │
│        + _get_active_macros │
└─────────────────────────────┘
```

### How to Use
```bash
# Start MCP server (stdio, used by LLM clients)
source .venv/bin/activate && python3 mcp_server.py

# Or test directly
python3 -c "
from mcp_server import read_c
print(read_c(file_path='main.c', target='PRODUCT_A', mode='physical'))
"
```

### Stage 2: Skeletonization — ✅ Complete

| Module | File | Status | Tests |
|--------|------|--------|-------|
| **Skeletonizer** | `skeletonizer.py` | ✅ Complete | 9/9 PASS |
| **MCP Tool** | `read_c_skeleton` in `mcp_server.py` | ✅ Complete | E2E verified |

**Behavior:** Strips function bodies `{ ... }` → `{ /* ... */ }`, preserves struct/union/enum/typedef definitions, `#define`/`#include` directives, and function signatures. Uses brace-counting state machine with string/comment awareness.

### Next Steps (not started)
- **Stage 3 (Dependency Graphing):** Parse `#include` trees for multi-file context optimization.
- **Stage 4 (Token Budgeting):** Rigid cap enforcement before LLM dispatch.
