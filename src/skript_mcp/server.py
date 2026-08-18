from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TypedDict

from aiomcrcon import Client
from mcp.server import MCPServer

from skript_mcp import __version__
from skript_mcp.parser import CheckResult, clean_line, parse_reload_output


_TEMP_NAME_RE = re.compile(r"^-?__mcp_check_[0-9a-f]{32}\.sk$")


class CommandResult(TypedDict):
    success: bool
    response: str


class LogsResult(TypedDict):
    success: bool
    content: str


class ResetResult(TypedDict):
    success: bool
    removed: list[str]
    errors: list[str]


@dataclass(frozen=True)
class Settings:
    scripts_dir: Path
    log_path: Path
    rcon_host: str
    rcon_port: int
    rcon_password: str
    rcon_timeout: float
    reload_timeout: float
    log_settle_time: float
    max_script_bytes: int
    max_log_bytes: int

    @classmethod
    def from_env(cls) -> Settings:
        password = os.environ.get("RCON_PASSWORD", "")
        if not password:
            raise RuntimeError("RCON_PASSWORD is required")

        return cls(
            scripts_dir=Path(os.environ.get("SKRIPT_SCRIPTS_DIR", "scripts")).resolve(),
            log_path=Path(
                os.environ.get("MINECRAFT_LOG_PATH", "runtime/logs/latest.log")
            ).resolve(),
            rcon_host=os.environ.get("RCON_HOST", "127.0.0.1"),
            rcon_port=int(os.environ.get("RCON_PORT", "25575")),
            rcon_password=password,
            rcon_timeout=float(os.environ.get("RCON_TIMEOUT", "5")),
            reload_timeout=float(os.environ.get("RELOAD_TIMEOUT", "30")),
            log_settle_time=float(os.environ.get("LOG_SETTLE_TIME", "0.25")),
            max_script_bytes=int(os.environ.get("MAX_SCRIPT_BYTES", "1048576")),
            max_log_bytes=int(os.environ.get("MAX_LOG_BYTES", "262144")),
        )


class RconGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def _execute_once(self, command: str) -> str:
        async with asyncio.timeout(self.settings.rcon_timeout):
            client = Client(
                self.settings.rcon_host,
                self.settings.rcon_port,
                self.settings.rcon_password,
            )
            try:
                await client.connect(timeout=self.settings.rcon_timeout)
                response, _request_id = await client.send_cmd(
                    command, timeout=self.settings.rcon_timeout
                )
                return response
            finally:
                await client.close()
                # aio-mc-rcon leaves the socket open when authentication fails.
                writer = getattr(client, "_writer", None)
                if writer is not None and not writer.is_closing():
                    writer.close()
                    await writer.wait_closed()

    async def health_check(self) -> None:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                await self._execute_once("list")
                return
            except Exception as error:
                last_error = error
                if attempt == 0:
                    await asyncio.sleep(0.25)
        raise RuntimeError("Paper RCON is unavailable") from last_error

    async def execute(self, command: str, *, check_health: bool = True) -> str:
        if check_health:
            await self.health_check()
        return await self._execute_once(command)


class LogCapture:
    """Capture bytes appended to latest.log, including across rotation."""

    def __init__(self, path: Path, max_bytes: int) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self._streams: dict[tuple[int, int], BinaryIO] = {}

    def start(self) -> None:
        self._add_current(start_at_end=True)

    def _add_current(self, *, start_at_end: bool) -> None:
        try:
            stream = self.path.open("rb")
        except FileNotFoundError:
            return
        stat = os.fstat(stream.fileno())
        key = (stat.st_dev, stat.st_ino)
        if key in self._streams:
            stream.close()
            return
        if start_at_end:
            stream.seek(0, os.SEEK_END)
        self._streams[key] = stream

    def _read_available(self, remaining: int) -> bytes:
        self._add_current(start_at_end=False)
        chunks: list[bytes] = []
        for stream in list(self._streams.values()):
            current = stream.tell()
            if os.fstat(stream.fileno()).st_size < current:
                stream.seek(0)
            chunk = stream.read(max(0, remaining - sum(map(len, chunks))))
            if chunk:
                chunks.append(chunk)
            if sum(map(len, chunks)) >= remaining:
                break
        return b"".join(chunks)

    async def collect(self, deadline: float, settle_time: float) -> str:
        output = bytearray()
        last_change = time.monotonic()
        while time.monotonic() < deadline and len(output) < self.max_bytes:
            chunk = await asyncio.to_thread(
                self._read_available, self.max_bytes - len(output)
            )
            if chunk:
                output.extend(chunk)
                last_change = time.monotonic()
            elif time.monotonic() - last_change >= settle_time:
                break
            await asyncio.sleep(0.05)
        return bytes(output).decode("utf-8", errors="replace")

    def close(self) -> None:
        for stream in self._streams.values():
            stream.close()
        self._streams.clear()


class SkriptRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.rcon = RconGateway(settings)
        self.operation_lock = asyncio.Lock()

    def _validate_script_path(self, script: str) -> tuple[Path, str]:
        if not script or any(char in script for char in "\r\n\x00"):
            raise ValueError("path must be a non-empty .sk file path")
        if "\\" in script:
            raise ValueError("script paths must use forward slashes")

        supplied = Path(script).expanduser()
        if supplied.name.startswith("-"):
            raise ValueError("disabled scripts cannot be checked")
        if supplied.suffix.casefold() != ".sk":
            raise ValueError("script path must end in .sk")

        root = self.settings.scripts_dir
        unresolved = supplied if supplied.is_absolute() else root / supplied
        if unresolved.is_symlink():
            raise ValueError("script does not exist or is not a regular file")
        candidate = unresolved.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("script path must stay inside the scripts directory")
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError("script does not exist or is not a regular file")
        return candidate, candidate.relative_to(root).as_posix()

    async def _reload(self, relative_name: str) -> CheckResult:
        return await self._run_reload(relative_name, relative_name)

    async def _reload_all(self, relative_name: str) -> CheckResult:
        return await self._run_reload("scripts", relative_name, all_scripts=True)

    async def _run_reload(
        self, reload_target: str, diagnostic_target: str, *, all_scripts: bool = False
    ) -> CheckResult:
        await self.rcon.health_check()
        capture = LogCapture(self.settings.log_path, self.settings.max_log_bytes)
        capture.start()
        deadline = time.monotonic() + self.settings.reload_timeout
        try:
            async with asyncio.timeout(self.settings.reload_timeout):
                response = await self.rcon.execute(
                    f"sk reload {reload_target}", check_health=False
                )
                log_output = await capture.collect(
                    deadline, self.settings.log_settle_time
                )
        except TimeoutError as error:
            raise RuntimeError(
                f"Skript reload timed out for {reload_target}"
            ) from error
        finally:
            capture.close()
        return parse_reload_output(
            response, log_output, diagnostic_target, all_scripts=all_scripts
        )

    async def check_file(self, script: str) -> CheckResult:
        path, relative_name = self._validate_script_path(script)
        if path.stat().st_size > self.settings.max_script_bytes:
            raise ValueError(
                f"script exceeds the {self.settings.max_script_bytes}-byte limit"
            )

        async with self.operation_lock:
            return await self._reload_all(relative_name)

    @staticmethod
    def _disable_succeeded(response: str, name: str) -> bool:
        cleaned = "\n".join(clean_line(line) for line in response.splitlines())
        return (
            re.search(
                rf"\bSuccessfully disabled\s+{re.escape(name)}\b",
                cleaned,
                re.IGNORECASE,
            )
            is not None
        )

    async def reload_file(self, script: str) -> CheckResult:
        async with self.operation_lock:
            _path, relative_name = self._validate_script_path(script)
            return await self._reload(relative_name)

    async def command(self, command: str) -> CommandResult:
        command = command.strip()
        if (
            not command
            or len(command) > 1446
            or any(char in command for char in "\r\n\x00")
        ):
            raise ValueError("command must be one line between 1 and 1446 characters")
        if command.startswith("/"):
            command = command[1:]
        async with self.operation_lock:
            response = await self.rcon.execute(command)
        return {"success": True, "response": response}

    async def logs(self, lines: int) -> LogsResult:
        if not 1 <= lines <= 2000:
            raise ValueError("lines must be between 1 and 2000")
        async with self.operation_lock:
            try:
                content = await asyncio.to_thread(
                    self._read_log_tail,
                    self.settings.log_path,
                    self.settings.max_log_bytes,
                )
            except FileNotFoundError:
                raise RuntimeError("Paper latest.log does not exist") from None
        selected = content.splitlines()[-lines:]
        return {"success": True, "content": "\n".join(selected)}

    @staticmethod
    def _read_log_tail(path: Path, max_bytes: int) -> str:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - max_bytes))
            content = stream.read(max_bytes)
        return content.decode("utf-8", errors="replace")

    async def reset(self) -> ResetResult:
        removed: list[str] = []
        errors: list[str] = []
        async with self.operation_lock:
            self.settings.scripts_dir.mkdir(parents=True, exist_ok=True)
            paths = [
                path
                for path in self.settings.scripts_dir.iterdir()
                if path.is_file()
                and not path.is_symlink()
                and _TEMP_NAME_RE.fullmatch(path.name)
            ]
            disabled_paths = [path for path in paths if path.name.startswith("-")]
            if disabled_paths:
                try:
                    response = await self.rcon.execute("sk reload scripts")
                    if not clean_line(response):
                        raise RuntimeError("Skript returned an empty reload response")
                except Exception as error:
                    errors.append(f"disabled temporary scripts: {error}")
                    disabled_paths = []

            for path in paths:
                enabled_name = path.name.removeprefix("-")
                try:
                    if path.name.startswith("-"):
                        if path not in disabled_paths:
                            continue
                    else:
                        response = await self.rcon.execute(f"sk disable {enabled_name}")
                        if not self._disable_succeeded(response, enabled_name):
                            raise RuntimeError(
                                f"unexpected disable response: {clean_line(response)}"
                            )
                    path.unlink(missing_ok=True)
                    (path.parent / f"-{enabled_name}").unlink(missing_ok=True)
                    removed.append(enabled_name)
                except Exception as error:
                    errors.append(f"{path.name}: {error}")
        return {
            "success": not errors,
            "removed": sorted(set(removed)),
            "errors": errors,
        }


_runtime: SkriptRuntime | None = None


def get_runtime() -> SkriptRuntime:
    global _runtime
    if _runtime is None:
        _runtime = SkriptRuntime(Settings.from_env())
    return _runtime


mcp = MCPServer("skript-mcp", version=__version__)


@mcp.tool()
async def skript_check_file(path: str) -> CheckResult:
    """Validate a .sk path inside SKRIPT_SCRIPTS_DIR with all sibling scripts loaded."""
    return await get_runtime().check_file(path)


@mcp.tool()
async def skript_reload_file(path: str) -> CheckResult:
    """Reload an existing .sk file relative to the configured scripts directory."""
    return await get_runtime().reload_file(path)


@mcp.tool()
async def minecraft_command(command: str) -> CommandResult:
    """Run a privileged Minecraft server command over local RCON."""
    return await get_runtime().command(command)


@mcp.tool()
async def minecraft_logs(lines: int = 200) -> LogsResult:
    """Return the newest bounded section of Paper's latest.log."""
    return await get_runtime().logs(lines)


@mcp.tool()
async def skript_reset() -> ResetResult:
    """Unload and remove only temporary scripts left by this MCP server."""
    return await get_runtime().reset()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
