import subprocess
import sys


def test_core_and_mcp_declarations_do_not_load_fastmcp() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; from fastapi_canon import Composition, Feature; from fastapi_canon.integrations.mcp import Inject, Mcp, McpScope; assert not any(name == 'fastmcp' or name.startswith('fastmcp.') for name in sys.modules); assert Feature(name='core').integrations == (); assert Mcp().path == '/mcp'; print('lazy imports verified')",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert result.stdout.strip() == "lazy imports verified"
