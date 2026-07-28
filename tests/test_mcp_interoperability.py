from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import unittest
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from erga_mcp.config import DEFAULT_CONFIG

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _assert_protocol_contract(read_stream: Any, write_stream: Any) -> None:
    async with ClientSession(read_stream, write_stream) as session:
        initialized = await session.initialize()
        assert "including a bare link" in (initialized.instructions or "")
        tools = await session.list_tools()
        by_name = {tool.name: tool for tool in tools.tools}
        assert {"erga_capabilities", "intake_job_url", "pipeline_status"}.issubset(by_name)
        assert by_name["intake_job_url"].inputSchema["required"] == ["job_url"]
        capabilities = await session.call_tool("erga_capabilities", {})
        assert not capabilities.isError
        status = await session.call_tool("pipeline_status", {})
        assert not status.isError


def _wait_for_loopback_server(process: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=10)
            raise AssertionError(
                f"Streamable HTTP server exited before accepting connections: "
                f"stdout={stdout!r}; stderr={stderr!r}"
            )
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise AssertionError("Streamable HTTP server did not begin listening within 15 seconds")


@contextmanager
def _running_http_server(config_path: Path, port: int) -> Iterator[subprocess.Popen[str]]:
    environment = {
        **os.environ,
        "ERGA_MCP_CONFIG": str(config_path),
        "ERGA_MCP_TRANSPORT": "streamable-http",
        "ERGA_MCP_HTTP_HOST": "127.0.0.1",
        "ERGA_MCP_HTTP_PORT": str(port),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "erga_mcp.mcp_server"],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        yield process
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


class McpInteroperabilityTests(unittest.TestCase):
    def test_official_python_sdk_uses_actual_streamable_http_server(self) -> None:
        async def connect(url: str) -> None:
            async with streamable_http_client(url) as (read_stream, write_stream, _):
                await _assert_protocol_contract(read_stream, write_stream)

        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            port = _free_loopback_port()
            with _running_http_server(config_path, port) as process:
                _wait_for_loopback_server(process, port)
                asyncio.run(connect(f"http://127.0.0.1:{port}/mcp"))

    def test_installed_wheel_stdio_server_works_without_mcp_extra(self) -> None:
        async def connect(command: str, config_path: Path, cwd: Path) -> None:
            parameters = StdioServerParameters(
                command=command,
                env={**os.environ, "ERGA_MCP_CONFIG": str(config_path)},
                cwd=cwd,
            )
            async with stdio_client(parameters) as (read_stream, write_stream):
                await _assert_protocol_contract(read_stream, write_stream)

        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            dist_dir = workspace / "dist"
            venv_dir = workspace / "venv"
            config_path = workspace / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            subprocess.run(
                ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            wheel = next(dist_dir.glob("erga_mcp-*.whl"))
            with zipfile.ZipFile(wheel) as archive:
                metadata_name = next(
                    name for name in archive.namelist() if name.endswith("METADATA")
                )
                metadata = archive.read(metadata_name).decode("utf-8")
            self.assertIn("Requires-Dist: mcp", metadata)
            self.assertNotIn("Provides-Extra: mcp", metadata)
            subprocess.run(
                ["uv", "venv", str(venv_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
            scripts_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
            python = scripts_dir / ("python.exe" if sys.platform == "win32" else "python")
            subprocess.run(
                ["uv", "pip", "install", "--python", str(python), str(wheel)],
                check=True,
                capture_output=True,
                text=True,
            )
            script = scripts_dir / ("erga-mcp.exe" if sys.platform == "win32" else "erga-mcp")
            self.assertTrue(script.is_file())
            asyncio.run(connect(str(script), config_path, workspace))


if __name__ == "__main__":
    unittest.main()
