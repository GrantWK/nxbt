"""Macro library: built-in example macros plus the user's own, in folders.

Each macro is a text file in nxbt's macro language (see docs/Macros.md) stored
at ``<root>/<folder>/<name>.txt``; the file name is the macro's title. A folder
is a ``/``-separated path up to ``MAX_FOLDER_DEPTH`` levels deep (for example
``Pokémon Legends Z-A/Wild zones``), or ``""`` for the top level. Built-in
macros ship with nxbt and are read-only; user macros live in the user's data
folder and take precedence when a folder and name match a built-in one.

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
MAX_FOLDER_DEPTH = 4
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


_NAME_RULE = "1-64 letters, digits, spaces or & ' ( ) , . + -"


def _check_name(label, value):
    if not isinstance(value, str) or not _SAFE_NAME.match(value):
        raise ValueError(f"{label} must be {_NAME_RULE}")


def normalize_folder(folder):
    """Checks a ``/``-separated folder path and returns it tidied: spaces
    around each ``/`` and leading or trailing slashes removed. ``""`` is the
    top level."""
    if not isinstance(folder, str):
        raise ValueError("Folder must be text")
    parts = [part.strip() for part in folder.strip().strip("/").split("/")]
    if parts == [""]:
        return ""
    if len(parts) > MAX_FOLDER_DEPTH:
        raise ValueError(f"Folders can be at most {MAX_FOLDER_DEPTH} levels deep")
    for part in parts:
        _check_name("Each folder name", part)
    return "/".join(parts)


class Library:
    def __init__(self, user_dir=None, builtin_dir=BUILTIN_DIR):
        self.user_dir = Path(user_dir) if user_dir else user_library_dir()
        self.builtin_dir = Path(builtin_dir)

    def list(self):
        """Folders that hold macros and their macros, sorted by path, the top
        level (``""``) first:
        ``[{"folder", "macros": [{"name", "source", "description", "setup"}]}]``."""
        folders = {}
        # User macros are read last so they replace built-ins of the same name
        for source, root in (("builtin", self.builtin_dir), ("user", self.user_dir)):
            if not root.is_dir():
                continue
            for path in root.rglob("*.txt"):
                parts = path.relative_to(root).parent.parts
                if len(parts) > MAX_FOLDER_DEPTH or any(p.startswith(".") for p in parts):
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                description, setup = read_header(text)
                folders.setdefault("/".join(parts), {})[path.stem] = {
                    "name": path.stem,
                    "source": source,
                    "description": description,
                    "setup": setup,
                }
        return [
            {"folder": folder, "macros": sorted(macros.values(), key=lambda m: m["name"].lower())}
            for folder, macros in sorted(
                folders.items(), key=lambda f: [p.lower() for p in f[0].split("/") if p]
            )
        ]

    def get(self, folder, name):
        """The macro's text and source. Raises FileNotFoundError if missing."""
        folder = normalize_folder(folder)
        for source, root in (("user", self.user_dir), ("builtin", self.builtin_dir)):
            path = self._path(root, folder, name)
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                return {"folder": folder, "name": name, "source": source, "text": text}
        raise FileNotFoundError(f"No macro {name!r} in {folder or 'the top level'!r}")

    def save(self, folder, name, text):
        """Saves a user macro, replacing any with the same folder and name.
        Returns the normalized folder."""
        if len(text.encode("utf-8")) > MAX_MACRO_BYTES:
            raise ValueError(f"Macro is larger than {MAX_MACRO_BYTES // 1024} KB")
        parse_macro(text)  # raises ValueError naming the bad line
        folder = normalize_folder(folder)
        path = self._path(self.user_dir, folder, name)
        self._makedirs(path.parent)
        path.write_text(text, encoding="utf-8")
        _give_to_invoking_user(path)
        return folder

    def delete(self, folder, name):
        """Deletes a user macro and any folders it leaves empty. Built-in
        macros can't be deleted."""
        folder = normalize_folder(folder)
        path = self._path(self.user_dir, folder, name)
        if not path.is_file():
            raise FileNotFoundError(f"No saved macro {name!r} in {folder or 'the top level'!r}")
        path.unlink()
        directory = path.parent
        while directory != self.user_dir and not any(directory.iterdir()):
            directory.rmdir()
            directory = directory.parent

    @staticmethod
    def _path(root, folder, name):
        _check_name("Macro name", name)
        return root.joinpath(*folder.split("/") if folder else (), f"{name}.txt")

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
