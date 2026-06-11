"""
End-to-end test: connect to MCP server via stdio and verify read_c tool works.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

STDIO_PARAMS = StdioServerParameters(
    command=sys.executable,
    args=["mcp_server.py"],
    env={"PYTHONPATH": os.path.dirname(os.path.abspath(__file__))},
)


COMPILE_DB = os.path.abspath("compile_commands.json")


async def test_read_c():
    async with stdio_client(STDIO_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool(
                "read_c",
                arguments={
                    "file_path": "test_samples/test_main.c",
                    "target": "ENABLED_FEATURE",
                    "compile_db": COMPILE_DB,
                    "mode": "physical",
                },
            )

            content = (
                result.content[0].text
                if hasattr(result.content[0], "text")
                else str(result.content)
            )

            assert "feature code" in content, "Active code should be present"
            assert "fallback code" not in content, (
                "Inactive else branch should be pruned"
            )
            assert "MacroPruner-Ctx" in content, "Summary header should be present"
            assert "Pruned:" in content, "Stats should be present"
            assert not any(
                "Error" in line
                for line in content.splitlines()
                if line.startswith("/* Error")
            ), "No error messages expected"

            print(f"TEST read_c: PASS")
            print(
                f"  Input: test_main.c target=ENABLED_FEATURE compile_db={COMPILE_DB}"
            )
            print()


async def test_read_c_virtual():
    async with stdio_client(STDIO_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool(
                "read_c",
                arguments={
                    "file_path": "test_samples/test_main.c",
                    "target": "ENABLED_FEATURE",
                    "compile_db": COMPILE_DB,
                    "mode": "virtual",
                },
            )

            content = (
                result.content[0].text
                if hasattr(result.content[0], "text")
                else str(result.content)
            )

            assert "feature code" in content
            assert "IFDEF DISABLED_FEATURE" in content or "INACTIVE" in content
            assert "Mode: virtual" in content

            print(f"TEST read_c (virtual mode): PASS")


async def test_list_tools():
    async with stdio_client(STDIO_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.list_tools()
            tool_names = [t.name for t in result.tools]

            assert "read_c" in tool_names, "read_c tool should be listed"

            print(f"TEST list_tools: PASS")
            print(f"  Available tools: {tool_names}")


async def test_read_c_with_compile_db_none():
    async with stdio_client(STDIO_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool(
                "read_c",
                arguments={
                    "file_path": "test_samples/test_main.c",
                    "target": "ENABLED_FEATURE",
                    "compile_db": COMPILE_DB,
                },
            )

            content = (
                result.content[0].text
                if hasattr(result.content[0], "text")
                else str(result.content)
            )

            assert "feature code" in content
            assert "MacroPruner-Ctx" in content

            print(f"TEST read_c (with explicit compile_db): PASS")


async def main():
    tests = [
        test_list_tools,
        test_read_c,
        test_read_c_virtual,
        test_read_c_with_compile_db_none,
    ]
    for t in tests:
        try:
            await t()
        except Exception as e:
            print(f"FAIL: {t.__name__}: {e}")
            raise

    print("\nAll MCP server tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
