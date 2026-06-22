"""Symlink runtime skills from ``.agents/skills`` into the provider's skills dir.

The agent runs with ``cwd=working_dir`` and scans the current provider's skills
directory (``.claude/skills`` for Claude, etc.). Linking (not copying) keeps
``.agents/skills`` the single source of truth.
"""

import logging
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / ".agents" / "skills"

# Per-provider skills directory (relative to working_dir) the agent scans.
PROVIDER_SKILLS_SUBDIR: dict[str, str] = {
    "claude": ".claude/skills",
    "codex": ".codex/skills",
    "pi": ".pi/skills",
}


def link_skills(
    working_dir: str | None, provider: str, glog: logging.Logger, name: str
) -> None:
    """Refresh our skill symlinks under ``<working_dir>/<provider skills dir>``.

    Existing real directories are left untouched; only our own stale symlinks
    are refreshed.
    """
    subdir = PROVIDER_SKILLS_SUBDIR.get(provider)
    if not working_dir or subdir is None or not SKILLS_DIR.is_dir():
        return
    target_root = Path(working_dir).expanduser() / subdir
    try:
        target_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        glog.warning("[%s] skills dir create failed %s: %s", name, target_root, exc)
        return
    for src in sorted(SKILLS_DIR.iterdir()):
        if not src.is_dir():
            continue
        link = target_root / src.name
        try:
            if link.is_symlink():
                if link.resolve() == src.resolve():
                    continue
                link.unlink()
            elif link.exists():
                glog.warning("[%s] skill not linked, real dir exists: %s", name, link)
                continue
            link.symlink_to(src, target_is_directory=True)
            glog.info("[%s] linked skill: %s -> %s", name, link.name, src)
        except OSError as exc:
            glog.warning("[%s] skill link failed %s: %s", name, link, exc)
