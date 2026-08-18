from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from skript_mcp.server import Settings, SkriptRuntime


class FakeRcon:
    def __init__(self, reload_response: str) -> None:
        self.reload_response = reload_response
        self.commands: list[str] = []
        self.health_checks = 0

    async def health_check(self) -> None:
        self.health_checks += 1

    async def execute(self, command: str, *, check_health: bool = True) -> str:
        self.commands.append(command)
        if command.startswith("sk reload"):
            return self.reload_response
        if command.startswith("sk disable"):
            return f"Successfully disabled {command.removeprefix('sk disable ')}."
        return "ok"


def settings(tmp_path: Path) -> Settings:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    logs = tmp_path / "logs" / "latest.log"
    logs.parent.mkdir()
    logs.write_text("existing log\n", encoding="utf-8")
    return Settings(
        scripts_dir=scripts,
        log_path=logs,
        rcon_host="127.0.0.1",
        rcon_port=25575,
        rcon_password="test-password",
        rcon_timeout=0.5,
        reload_timeout=1,
        log_settle_time=0,
        max_script_bytes=1024,
        max_log_bytes=4096,
    )


@pytest.mark.asyncio
async def test_check_uses_unique_file_and_cleans_up(tmp_path: Path) -> None:
    runtime = SkriptRuntime(settings(tmp_path))
    fake = FakeRcon("")
    runtime.rcon = fake  # type: ignore[assignment]

    async def reload_with_name(relative_name: str):
        assert (
            runtime.settings.scripts_dir / relative_name
        ).read_text() == "on load:\n\tstop"
        return {
            "success": True,
            "errors": [],
            "raw_output": f"Successfully reloaded {relative_name}",
        }

    runtime._reload = reload_with_name  # type: ignore[method-assign]
    result = await runtime.check_file("on load:\n\tstop")

    assert result["success"] is True
    assert fake.health_checks == 0
    assert len(fake.commands) == 1
    assert fake.commands[0].startswith("sk disable __mcp_check_")
    assert list(runtime.settings.scripts_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_check_cleans_up_when_reload_fails(tmp_path: Path) -> None:
    runtime = SkriptRuntime(settings(tmp_path))
    fake = FakeRcon("")
    runtime.rcon = fake  # type: ignore[assignment]

    async def failed_reload(_relative_name: str):
        raise RuntimeError("reload failed")

    runtime._reload = failed_reload  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="reload failed"):
        await runtime.check_file("bad syntax")

    assert list(runtime.settings.scripts_dir.iterdir()) == []
    assert any(command.startswith("sk disable") for command in fake.commands)


@pytest.mark.asyncio
async def test_check_retains_disabled_marker_when_unload_fails(tmp_path: Path) -> None:
    runtime = SkriptRuntime(settings(tmp_path))

    class FailedDisableRcon(FakeRcon):
        async def execute(self, command: str, *, check_health: bool = True) -> str:
            if command.startswith("sk disable"):
                return "Could not disable that script"
            return await super().execute(command, check_health=check_health)

    runtime.rcon = FailedDisableRcon("")  # type: ignore[assignment]

    async def successful_reload(relative_name: str):
        return {"success": True, "errors": [], "raw_output": relative_name}

    runtime._reload = successful_reload  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="retained"):
        await runtime.check_file("on join:\n    stop")

    markers = list(runtime.settings.scripts_dir.glob("-__mcp_check_*.sk"))
    assert len(markers) == 1


@pytest.mark.asyncio
async def test_reload_rejects_paths_outside_scripts(tmp_path: Path) -> None:
    runtime = SkriptRuntime(settings(tmp_path))

    with pytest.raises(ValueError):
        await runtime.reload_file("../outside.sk")


@pytest.mark.asyncio
async def test_commands_are_serialized(tmp_path: Path) -> None:
    runtime = SkriptRuntime(settings(tmp_path))
    active = 0
    maximum_active = 0

    class SlowRcon(FakeRcon):
        async def execute(self, command: str, *, check_health: bool = True) -> str:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            return command

    runtime.rcon = SlowRcon("")  # type: ignore[assignment]
    await asyncio.gather(runtime.command("list"), runtime.command("plugins"))

    assert maximum_active == 1


@pytest.mark.asyncio
async def test_reset_only_removes_reserved_files(tmp_path: Path) -> None:
    runtime = SkriptRuntime(settings(tmp_path))
    fake = FakeRcon("")
    runtime.rcon = fake  # type: ignore[assignment]
    stale = runtime.settings.scripts_dir / f"__mcp_check_{'a' * 32}.sk"
    user_script = runtime.settings.scripts_dir / "user.sk"
    stale.write_text("bad", encoding="utf-8")
    user_script.write_text("keep", encoding="utf-8")

    result = await runtime.reset()

    assert result["success"] is True
    assert not stale.exists()
    assert user_script.exists()


@pytest.mark.asyncio
async def test_logs_reads_only_bounded_tail(tmp_path: Path) -> None:
    config = settings(tmp_path)
    config.log_path.write_text("discard\nkeep-one\nkeep-two\n", encoding="utf-8")
    runtime = SkriptRuntime(
        Settings(**{**config.__dict__, "max_log_bytes": len("keep-one\nkeep-two\n")})
    )

    result = await runtime.logs(2)

    assert result["content"] == "keep-one\nkeep-two"
