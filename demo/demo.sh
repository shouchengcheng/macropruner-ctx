#!/bin/bash
# End-to-end demo script for macropruner-ctx.
#
# What this demonstrates, in order:
#   1. The core compression funnel on a synthetic C file
#   2. Token budget auto-degradation
#   3. Multi-file context (read_c_with_deps)
#   4. apply_patch (no git required)
#   5. Cross-validation via clang backend
#
# Run from any directory; it sets up its own working dir under /tmp.
#
# Usage: bash demo/demo.sh [--record path/to/output.log]
#
# Each step pauses briefly so a screencast can show the output
# before the next step scrolls past. Use --no-pause to skip the
# pauses (for batch runs / CI).

set -e

# -- Args --
RECORD=""
PAUSE=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --record)  RECORD="$2"; shift 2 ;;
        --no-pause) PAUSE=0; shift ;;
        *)         echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

pause() {
    if [[ $PAUSE -eq 1 ]]; then
        sleep "${1:-1.5}"
    fi
}

# Tee everything to a log file if --record was given.
if [[ -n "$RECORD" ]]; then
    mkdir -p "$(dirname "$RECORD")"
    exec > >(tee -a "$RECORD") 2>&1
fi

# -- Setup --
DEMO_DIR=$(mktemp -d /tmp/macropruner_demo_XXXX)
trap "rm -rf $DEMO_DIR" EXIT
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PY="$ROOT/.venv/bin/python"

# Helper: bold + clear section dividers for the recording.
hr()    { printf '\n%s\n' "================================================================"; }
title() { hr; printf '== %s\n' "$1"; hr; }

# Continue past non-zero exits from individual tool calls —
# we want the demo to show every step even when one of them
# returns a failure code (e.g. the diff oracle disagreeing).
set +e

title "MacroPruner-Ctx end-to-end demo"
echo "Demo dir: $DEMO_DIR"
echo "Tool root: $ROOT"
echo
pause 2

# -- Step 1: basic setup --
title "Step 1 — Set up a sample C project with #if chaos"
cat > "$DEMO_DIR/main.c" <<'EOF'
#include <stdio.h>

#if PRODUCT_TYPE == 3
    #define MAX_CONN 8
    void init_product3(void) {
        printf("Product 3 ready, %d conns\n", MAX_CONN);
        configure_wifi();
        configure_ble();
    }
#elif PRODUCT_TYPE == 5
    #define MAX_CONN 32
    void init_product5(void) {
        printf("Product 5 ready, %d conns\n", MAX_CONN);
        configure_wifi();
        configure_zigbee();
    }
#else
    #define MAX_CONN 4
    void init_default(void) {
        printf("Default product, %d conns\n", MAX_CONN);
    }
#endif

#if defined(HAS_WIFI) && defined(HAS_BLE)
void init_dual_radio(void) { printf("dual radio\n"); }
#endif

#ifdef DEBUG
static int dbg_level = 2;
static int dbg_timestamp = 1;
#endif

int main(void) { return 0; }
EOF
cat > "$DEMO_DIR/compile_commands.json" <<'EOF'
[{"directory": "/__will_be_replaced__", "command": "gcc -DPRODUCT_TYPE=3 -DHAS_WIFI -DDEBUG -c main.c -o main.o", "file": "main.c"}]
EOF
# Inject the actual demo dir into the cdb.
sed -i "s|/__will_be_replaced__|$DEMO_DIR|g" "$DEMO_DIR/compile_commands.json"

cat > "$DEMO_DIR/.macroprunerrc" <<'EOF'
default_target = PRODUCT_3
default_backend = regex
default_mode = physical
EOF

echo "Sample C project created at $DEMO_DIR"
echo
ls -la "$DEMO_DIR"
echo
pause 3

# -- Step 2: bare read_c (MCP equivalent) --
title "Step 2 — read_c: prune inactive #if blocks"
echo "MCP call: read_c(file_path='$DEMO_DIR/main.c')"
echo "(no target or compile_db given; .macroprunerrc supplies both)"
echo
( cd "$DEMO_DIR" && "$PY" -c "
import asyncio, sys
sys.path.insert(0, '$ROOT')
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

async def go():
    p = StdioServerParameters(command='$PY', args=['$ROOT/mcp_server.py'], cwd='$DEMO_DIR')
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool('read_c', {'file_path': '$DEMO_DIR/main.c'})
            print(res.content[0].text)
asyncio.run(go())
" )
echo
pause 3

# -- Step 3: token budget auto-degradation --
title "Step 3 — Token budget auto-degradation"
echo "Same file, but with token_budget=80 (pruned code is much larger)"
echo
( cd "$DEMO_DIR" && "$PY" -c "
import asyncio, sys
sys.path.insert(0, '$ROOT')
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

async def go():
    p = StdioServerParameters(command='$PY', args=['$ROOT/mcp_server.py'], cwd='$DEMO_DIR')
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool('read_c', {'file_path': '$DEMO_DIR/main.c', 'token_budget': 80})
            print(res.content[0].text)
asyncio.run(go())
" )
echo
pause 3

# -- Step 4: skeleton --
title "Step 4 — read_c_skeleton: function bodies stripped"
echo "Best for getting a quick module overview"
echo
( cd "$DEMO_DIR" && "$PY" -c "
import asyncio, sys
sys.path.insert(0, '$ROOT')
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

async def go():
    p = StdioServerParameters(command='$PY', args=['$ROOT/mcp_server.py'], cwd='$DEMO_DIR')
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool('read_c_skeleton', {'file_path': '$DEMO_DIR/main.c'})
            print(res.content[0].text)
asyncio.run(go())
" )
echo
pause 3

# -- Step 5: read_c_with_deps --
title "Step 5 — read_c_with_deps: cross-file context"
# Create a header with a #include that should NOT be followed (it's
# in an inactive #if block). This is the Stage 3 Phase 2 demo.
cat > "$DEMO_DIR/proto.h" <<'EOF'
#if PRODUCT_TYPE == 3
    int product3_proto_id = 3;
#else
    int product5_proto_id = 5;
#endif

#if defined(EXPERIMENTAL)
    int experimental_proto_feature(void);
#endif
EOF
# Add the include to main.c.
sed -i '1i\#include "proto.h"' "$DEMO_DIR/main.c"

echo "Now main.c has #include \"proto.h\" with a conditional branch."
echo "Stage 3 Phase 2 only follows the include path for ACTIVE target."
echo
( cd "$DEMO_DIR" && "$PY" -c "
import asyncio, sys
sys.path.insert(0, '$ROOT')
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

async def go():
    p = StdioServerParameters(command='$PY', args=['$ROOT/mcp_server.py'], cwd='$DEMO_DIR')
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool('read_c_with_deps', {'file_path': '$DEMO_DIR/main.c', 'max_depth': 3})
            print(res.content[0].text)
asyncio.run(go())
" )
echo
pause 3

# -- Step 6: apply_patch (no git required) --
title "Step 6 — apply_patch: LLM-generated diff, no git required"
echo "This is a non-git project. patch_applier kicks in."
echo

# Write the diff to a temp file so we don't have to deal with
# shell escaping through Python.
DIFF_FILE=$(mktemp)
cat > "$DIFF_FILE" <<EOF
--- a/$DEMO_DIR/main.c
+++ b/$DEMO_DIR/main.c
@@ -3,6 +3,7 @@
 
 #if PRODUCT_TYPE == 3
     #define MAX_CONN 8
+    #define ENABLE_SECURE_BOOT 1
     void init_product3(void) {
         printf("Product 3 ready, %d conns\\n", MAX_CONN);
EOF

( cd "$DEMO_DIR" && "$PY" -c "
import asyncio, sys
sys.path.insert(0, '$ROOT')
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

DIFF = open('$DIFF_FILE').read()

async def go():
    p = StdioServerParameters(command='$PY', args=['$ROOT/mcp_server.py'], cwd='$DEMO_DIR')
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool('apply_patch', {'file_path': '$DEMO_DIR/main.c', 'diff': DIFF})
            print(res.content[0].text)
asyncio.run(go())
" )
rm -f "$DIFF_FILE"
echo
echo "After patch (showing the inserted line):"
grep -E 'ENABLE_SECURE_BOOT' "$DEMO_DIR/main.c" || echo "patch did not apply"
echo
pause 3

# -- Step 7: CLI without MCP --
title "Step 7 — CLI mode (no MCP server required)"
echo
echo "Same project, but driven by cli.py — useful for CI, ad-hoc inspection, Makefile."
echo
echo '$ cli.py read main.c'
( cd "$DEMO_DIR" && "$PY" "$ROOT/cli.py" read main.c --target PRODUCT_3 | head -10 )
echo
echo '$ cli.py skeleton main.c'
( cd "$DEMO_DIR" && "$PY" "$ROOT/cli.py" skeleton main.c --target PRODUCT_3 | head -10 )
echo
echo '$ cli.py diff main.c (regex vs clang oracle)'
( cd "$DEMO_DIR" && "$PY" "$ROOT/cli.py" diff main.c --target PRODUCT_3 )
echo
pause 3

# -- Step 8: error handling --
title "Step 8 — Tagged error handling"
echo "When a tool call fails, the response is tagged with severity."
echo "LLMs can grep for [FATAL] / [WARN] to decide whether to retry."
echo
( cd "$DEMO_DIR" && "$PY" -c "
import asyncio, sys
sys.path.insert(0, '$ROOT')
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

async def go():
    p = StdioServerParameters(command='$PY', args=['$ROOT/mcp_server.py'], cwd='$DEMO_DIR')
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            # Non-existent file
            res = await s.call_tool('read_c', {'file_path': '/no/such/file.c'})
            print('Non-existent file:')
            print('  ', res.content[0].text.split(chr(10))[0])
            print()
            # Unknown backend falls back to regex
            res = await s.call_tool('read_c', {'file_path': '$DEMO_DIR/main.c', 'backend': 'bogus'})
            print('Unknown backend (falls back to regex):')
            print('  ', res.content[0].text.split(chr(10))[5])  # the Backend: line
asyncio.run(go())
" )
echo
pause 3

# -- Wrap up --
title "Done"
echo
echo "What you just saw:"
echo "  - read_c / read_c_skeleton / read_c_with_deps for context"
echo "  - token_budget auto-degradation when output exceeds cap"
echo "  - apply_patch on a non-git project"
echo "  - standalone CLI for non-MCP workflows"
echo "  - Tagged [FATAL] / [WARN] error handling"
echo
echo "Try it on your own project:"
echo "  1. Drop a .macroprunerrc in your project root"
echo "  2. Add macropruner to your agent (hermes mcp add macropruner ...)"
echo "  3. Agent prompt: 'When read_c returns [FATAL], adjust args and retry.'"
echo
echo "Docs: README.md, docs/usage.md, INTEGRATION.md"
echo "Real SDK integration: integration/ws63_integration_report.md"
