"""Macro library: built-in example macros plus the user's own, grouped by game.

Each macro is a text file in nxbt's macro language (see docs/Macros.md) stored
at ``<root>/<game>/<name>.txt``; the file name is the macro's title. Built-in
macros ship with nxbt and are read-only; user macros live in the user's data
folder and take precedence when a name matches a built-in one.

A macro starts with ``#`` comment lines shown in the web UI::

    # What the macro does.
    # Before you start: where to be and what must be true first.
"""

import os
import re
from pathlib import Path

from ..controller.macro import parse_macro

BUILTIN_DIR = Path(__file__).parent / "builtin"
MAX_MACRO_BYTES = 256 * 1024
# Letters, digits and a few punctuation marks; no slashes, no leading dot
_SAFE_NAME = re.compile(r"^\w[\w &'(),.+-]{0,63}$")


def user_library_dir():
    """``~/.local/share/nxbt/library`` for the user running nxbt. Under sudo
    this is the invoking user's folder, not root's."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        import pwd

        data = Path(pwd.getpwnam(sudo_user).pw_dir) / ".local" / "share"
    else:
        data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return data / "nxbt" / "library"


SETUP_PREFIX = "before you start:"


def read_header(text):
    """Splits the leading ``#`` lines into (description, setup)."""
    description, setup = [], []
    current = description
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        line = line.lstrip("#").strip()
        if line.lower().startswith(SETUP_PREFIX):
            current = setup
            line = line[len(SETUP_PREFIX):].strip()
        current.append(line)
    return " ".join(description).strip(), " ".join(setup).strip()


def _give_to_invoking_user(path):
    """Files created under sudo would otherwise belong to root."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid and gid:
        os.chown(path, int(uid), int(gid))


class Library:
    def __init__(self, user_dir=None, builtin_dir=BUILTIN_DIR):
        self.user_dir = Path(user_dir) if user_dir else user_library_dir()
        self.builtin_dir = Path(builtin_dir)

    def list(self):
        """Games and their macros, sorted by name:
        ``[{"game", "macros": [{"name", "source", "description", "setup"}]}]``."""
        games = {}
        # User macros are read last so they replace built-ins of the same name
        for source, root in (("builtin", self.builtin_dir), ("user", self.user_dir)):
            if not root.is_dir():
                continue
            for path in root.glob("*/*.txt"):
                text = path.read_text(encoding="utf-8", errors="replace")
                description, setup = read_header(text)
                games.setdefault(path.parent.name, {})[path.stem] = {
                    "name": path.stem,
                    "source": source,
                    "description": description,
                    "setup": setup,
                }
        return [
            {"game": game, "macros": sorted(macros.values(), key=lambda m: m["name"].lower())}
            for game, macros in sorted(games.items(), key=lambda g: g[0].lower())
        ]

    def get(self, game, name):
        """The macro's text and source. Raises FileNotFoundError if missing."""
        for source, root in (("user", self.user_dir), ("builtin", self.builtin_dir)):
            path = self._path(root, game, name)
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                return {"game": game, "name": name, "source": source, "text": text}
        raise FileNotFoundError(f"No macro {name!r} for {game!r}")

    def save(self, game, name, text):
        """Saves a user macro, replacing any with the same game and name."""
        if len(text.encode("utf-8")) > MAX_MACRO_BYTES:
            raise ValueError(f"Macro is larger than {MAX_MACRO_BYTES // 1024} KB")
        parse_macro(text)  # raises ValueError naming the bad line
        path = self._path(self.user_dir, game, name)
        self._makedirs(path.parent)
        path.write_text(text, encoding="utf-8")
        _give_to_invoking_user(path)

    def delete(self, game, name):
        """Deletes a user macro. Built-in macros can't be deleted."""
        path = self._path(self.user_dir, game, name)
        if not path.is_file():
            raise FileNotFoundError(f"No saved macro {name!r} for {game!r}")
        path.unlink()
        if not any(path.parent.iterdir()):
            path.parent.rmdir()

    @staticmethod
    def _path(root, game, name):
        for label, value in (("Game", game), ("Macro name", name)):
            if not isinstance(value, str) or not _SAFE_NAME.match(value):
                raise ValueError(
                    f"{label} must be 1-64 letters, digits, spaces or & ' ( ) , . + -"
                )
        return root / game / f"{name}.txt"

    def _makedirs(self, directory):
        """Creates missing folders one level at a time so each new one can be
        handed to the invoking user."""
        missing = []
        while not directory.exists():
            missing.append(directory)
            directory = directory.parent
        for new_dir in reversed(missing):
            new_dir.mkdir()
            _give_to_invoking_user(new_dir)
