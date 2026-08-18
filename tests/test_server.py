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
async def test_check_reloads_all_scripts_for_shared_functions(tmp_path: Path) -> None:
    runtime = SkriptRuntime(settings(tmp_path))
    fake = FakeRcon("Successfully reloaded all scripts. (2ms)")
    runtime.rcon = fake  # type: ignore[assignment]
    target = runtime.settings.scripts_dir / "features" / "target.sk"
    target.parent.mkdir()
    target.write_text("on load:\n    stop", encoding="utf-8")

    result = await runtime.check_file("features/target.sk")

    assert result["success"] is True
    assert fake.health_checks == 1
    assert len(fake.commands) == 1
    assert fake.commands[0] == "sk reload scripts"


@pytest.mark.asyncio
async def test_check_accepts_absolute_path_inside_scripts(tmp_path: Path) -> None:
    runtime = SkriptRuntime(settings(tmp_path))
    target = runtime.settings.scripts_dir / "target.sk"
    target.write_text("on load:\n    stop", encoding="utf-8")
    checked: list[str] = []

    async def reload_all(relative_name: str):
        checked.append(relative_name)
        return {"success": True, "errors": [], "raw_output": "ok"}

    runtime._reload_all = reload_all  # type: ignore[method-assign]
    result = await runtime.check_file(str(target))

    assert result["success"] is True
    assert checked == ["target.sk"]


@pytest.mark.asyncio
async def test_reload_rejects_paths_outside_scripts(tmp_path: Path) -> None:
    runtime = SkriptRuntime(settings(tmp_path))

    with pytest.raises(ValueError):
        await runtime.reload_file("../outside.sk")


@pytest.mark.asyncio
async def test_check_rejects_paths_outside_scripts(tmp_path: Path) -> None:
    runtime = SkriptRuntime(settings(tmp_path))
    outside = tmp_path / "outside.sk"
    outside.write_text("on load:\n    stop", encoding="utf-8")

    with pytest.raises(ValueError, match="inside the scripts directory"):
        await runtime.check_file(str(outside))


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
