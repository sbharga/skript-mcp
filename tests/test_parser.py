from skript_mcp.parser import clean_line, parse_reload_output


def test_parses_skript_error_block() -> None:
    output = """§cLine 3: (__mcp_check_a.sk)
    Can't understand this condition/effect: launch the moon
    Line: launch the moon

Encountered 1 error while reloading __mcp_check_a.sk! (15ms)
"""

    result = parse_reload_output(output, "", "__mcp_check_a.sk")

    assert result["success"] is False
    assert result["errors"] == [
        {
            "path": "__mcp_check_a.sk",
            "line": 3,
            "message": "Can't understand this condition/effect: launch the moon",
        }
    ]
    assert output.strip() in result["raw_output"]


def test_merges_and_deduplicates_rcon_and_log_errors() -> None:
    diagnostic = """Line 8: (folder/example.sk)
    Can't understand this structure: every banana:
    Line: every banana:
Encountered 1 error while reloading folder/example.sk! (5ms)"""

    result = parse_reload_output(diagnostic, diagnostic, "folder/example.sk")

    assert result["errors"] == [
        {
            "path": "folder/example.sk",
            "line": 8,
            "message": "Can't understand this structure: every banana:",
        }
    ]


def test_success_requires_matching_terminal_message() -> None:
    result = parse_reload_output(
        "Successfully reloaded other.sk. (2ms)",
        "[12:00:00 INFO]: Successfully reloaded target.sk. (3ms)",
        "target.sk",
    )

    assert result["success"] is True
    assert result["errors"] == []


def test_similar_filename_does_not_match() -> None:
    result = parse_reload_output(
        "Successfully reloaded not-target.sk. (2ms)", "", "target.sk"
    )

    assert result["success"] is False


def test_error_summary_without_line_has_fallback() -> None:
    result = parse_reload_output(
        "Encountered 2 errors while reloading target.sk!", "", "target.sk"
    )

    assert result["success"] is False
    assert result["errors"][0]["line"] == 0


def test_all_scripts_reload_reports_dependency_errors() -> None:
    output = """Line 4: (lib/utils.sk)
    Can't understand this condition/effect: launch the moon
    Line: launch the moon
Encountered 1 error while reloading all scripts! (5ms)"""

    result = parse_reload_output(output, "", "features/target.sk", all_scripts=True)

    assert result["success"] is False
    assert result["errors"] == [
        {
            "path": "lib/utils.sk",
            "line": 4,
            "message": "Can't understand this condition/effect: launch the moon",
        }
    ]


def test_all_scripts_reload_recognizes_success() -> None:
    result = parse_reload_output(
        "Successfully reloaded all scripts. (2ms)",
        "",
        "features/target.sk",
        all_scripts=True,
    )

    assert result["success"] is True
    assert result["errors"] == []


def test_clean_line_removes_console_and_minecraft_formatting() -> None:
    assert clean_line("[12:00:00 INFO]: §x§f§f§0§0§0§0§lMessage") == "Message"
