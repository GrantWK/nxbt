"""Macro library: built-in and user macros, storage location and name safety."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nxbt.library import BUILTIN_DIR, MAX_MACRO_BYTES, Library, read_header, user_library_dir


@pytest.fixture
def library(tmp_path):
    builtin = tmp_path / "builtin" / "General"
    builtin.mkdir(parents=True)
    (builtin / "Mash A.txt").write_text("# Mashes A.\nA 0.1s\n")
    return Library(user_dir=tmp_path / "user", builtin_dir=tmp_path / "builtin")


def test_read_header():
    text = "# Does a thing.\n# More detail.\n# Before you start: stand here,\n# facing north.\nA 0.1s\n# not header\n"
    assert read_header(text) == ("Does a thing. More detail.", "stand here, facing north.")


def test_lists_builtin_macros(library):
    [game] = library.list()
    assert game["game"] == "General"
    assert game["macros"] == [
        {"name": "Mash A", "source": "builtin", "description": "Mashes A.", "setup": ""}
    ]


def test_user_macro_saves_and_overrides_builtin(library):
    library.save("General", "Mash A", "# Mine.\nB 0.1s\n")
    library.save("Pokémon Legends Z-A", "Turbo A", "A 0.4s\n")

    games = {g["game"]: g["macros"] for g in library.list()}
    assert games["General"][0]["source"] == "user"
    assert library.get("General", "Mash A")["text"] == "# Mine.\nB 0.1s\n"
    assert library.get("Pokémon Legends Z-A", "Turbo A")["source"] == "user"


def test_delete_user_macro_reveals_builtin_again(library):
    library.save("General", "Mash A", "B 0.1s\n")
    library.delete("General", "Mash A")
    assert library.get("General", "Mash A")["source"] == "builtin"


def test_builtin_macros_cannot_be_deleted(library):
    with pytest.raises(FileNotFoundError):
        library.delete("General", "Mash A")


@pytest.mark.parametrize("name", ["", "../escape", "a/b", ".hidden", "x" * 65, "back\\slash"])
def test_rejects_unsafe_names(library, name):
    with pytest.raises(ValueError):
        library.save("General", name, "A 0.1s\n")
    with pytest.raises(ValueError):
        library.save(name, "Macro", "A 0.1s\n")


def test_rejects_oversized_macro(library):
    with pytest.raises(ValueError):
        library.save("General", "Huge", "A" * (MAX_MACRO_BYTES + 1))


def test_user_dir_under_sudo_is_the_invoking_users(monkeypatch):
    monkeypatch.setenv("SUDO_USER", "alice")
    with patch("pwd.getpwnam", return_value=MagicMock(pw_dir="/home/alice")):
        assert user_library_dir() == Path("/home/alice/.local/share/nxbt/library")


def test_files_saved_under_sudo_belong_to_invoking_user(library, monkeypatch):
    monkeypatch.setenv("SUDO_UID", "1000")
    monkeypatch.setenv("SUDO_GID", "1000")
    with patch("nxbt.library.os.chown") as chown:
        library.save("New Game", "Macro", "A 0.1s\n")

    chowned = {Path(call.args[0]).name for call in chown.call_args_list}
    assert {"user", "New Game", "Macro.txt"} <= chowned


def test_shipped_macros_have_descriptions():
    for path in BUILTIN_DIR.glob("*/*.txt"):
        description, setup = read_header(path.read_text(encoding="utf-8"))
        assert description and setup, path.name


def make_web_app(library):
    from nxbt.web.app import WebApp

    return WebApp(nxbt=MagicMock(), library=library)


def test_web_save_then_list(library):
    app = make_web_app(library)
    with patch.object(app, "_emit_to") as emit:
        app.handle_library_save("sid", json.dumps(["General", "Mine", "A 0.1s\n"]))

    events = [call.args[1] for call in emit.call_args_list]
    assert events == ["library", "library_macro"]


def test_web_reports_invalid_names(library):
    app = make_web_app(library)
    with patch.object(app, "_emit_to") as emit:
        app.handle_library_save("sid", json.dumps(["General", "../x", "A 0.1s\n"]))

    assert emit.call_args.args[1] == "library_error"


def test_invalid_macros_are_not_saved(library):
    with pytest.raises(ValueError, match="Line 2"):
        library.save("General", "Typo", "A\nHOM\n")
    assert not (library.user_dir / "General" / "Typo.txt").exists()
