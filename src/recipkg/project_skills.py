"""Manage a project's full Agent Skills as pinned Git submodules."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit

from .package import RecipeError
from .skills import _validate_skill_bytes
from .store import NAME

REV = re.compile(r"[0-9a-f]{40}\Z")


def _git(directory: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in list(env):
        if key in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                   "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
                   "GIT_CEILING_DIRECTORIES", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"} or key.startswith(
                       ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
                   ):
            env.pop(key)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=always", *args],
            cwd=directory, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecipeError(f"Git failed: {exc}") from exc
    if check and result.returncode:
        raise RecipeError(f"Git failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def _root(project: Path) -> Path:
    project = project.absolute()
    if not project.is_dir() or project.is_symlink():
        raise RecipeError("Project must be an existing Git working directory")
    result = _git(project, "rev-parse", "--show-toplevel")
    root = Path(result.stdout.strip()).resolve()
    if project.resolve() != root:
        raise RecipeError("Project must be the Git working tree root")
    return root


def _name(name: str) -> None:
    if len(name) > 64 or not NAME.fullmatch(name):
        raise RecipeError("Skill name must be lowercase words separated by hyphens (max 64 characters)")


def _rev(rev: str) -> None:
    if not REV.fullmatch(rev):
        raise RecipeError("Skill revision must be a full 40-character lowercase commit hash")


def _source(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password and not parsed.fragment:
        return
    if Path(url).is_absolute() and Path(url).exists():
        return
    raise RecipeError("Skill source must be an HTTPS URL without credentials or an existing absolute local path")


def _paths(root: Path, name: str) -> tuple[Path, str]:
    _name(name)
    rel = f".agents/skills/{name}"
    for item in (root / ".agents", root / ".agents/skills", root / rel):
        if item.is_symlink():
            raise RecipeError(f"Refusing symbolic link in skill path: {item}")
    return root / rel, rel


def _entries(root: Path) -> dict[str, tuple[str, str]]:
    modules = root / ".gitmodules"
    if modules.is_symlink():
        raise RecipeError("Refusing symbolic .gitmodules")
    if not modules.exists():
        return {}
    output = _git(root, "config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$", check=False)
    if output.returncode not in (0, 1):
        raise RecipeError("Cannot read .gitmodules")
    entries: dict[str, tuple[str, str]] = {}
    for line in output.stdout.splitlines():
        key, _, path = line.partition(" ")
        parts = path.split("/")
        if len(parts) != 3 or parts[:2] != [".agents", "skills"]:
            continue
        _name(parts[2])
        module = key[len("submodule."):-len(".path")]
        url = _git(root, "config", "--file", ".gitmodules", "--get", f"submodule.{module}.url").stdout.strip()
        if parts[2] in entries:
            raise RecipeError(f"Duplicate project skill: {parts[2]}")
        entries[parts[2]] = (module, url)
    return entries


def _validate_at(repo: Path, rev: str, name: str) -> None:
    commit = _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").stdout.strip()
    if commit != rev:
        raise RecipeError("Revision does not identify a commit")
    text = _git(repo, "show", f"{rev}:SKILL.md").stdout
    skill_name, _ = _validate_skill_bytes(text.encode("utf-8"), f"{rev}:SKILL.md")
    if skill_name != name:
        raise RecipeError(f"Skill name {skill_name!r} does not match {name!r}")


def list_project_skills(project: Path) -> list[tuple[str, str, str]]:
    root = _root(project)
    result = []
    for name, (_, url) in sorted(_entries(root).items()):
        _, rel = _paths(root, name)
        stage = _git(root, "ls-files", "--stage", "--", rel).stdout.strip().split()
        if len(stage) < 4 or stage[0] != "160000" or stage[2] != "0":
            raise RecipeError(f"Project skill is not a valid gitlink: {rel}")
        result.append((name, url, stage[1]))
    return result


def add_project_skill(project: Path, url: str, name: str, rev: str) -> None:
    root = _root(project)
    target, rel = _paths(root, name)
    _source(url)
    _rev(rev)
    module = f"recipkg-skill-{name}"
    if name in _entries(root) or target.exists():
        raise RecipeError(f"Skill path already exists: {rel}")
    ignored = _git(root, "check-ignore", "--no-index", "-q", "--", rel, check=False)
    if ignored.returncode == 0:
        raise RecipeError(f"Skill path is ignored by Git: {rel}")
    if ignored.returncode != 1:
        raise RecipeError("Cannot check Git ignore rules for skill path")
    if _git(root, "status", "--porcelain", "--", ".gitmodules", rel).stdout:
        raise RecipeError("Commit or preserve existing .gitmodules/skill changes first")
    module_dir = Path(_git(root, "rev-parse", "--git-path", f"modules/{module}").stdout.strip())
    if not module_dir.is_absolute():
        module_dir = root / module_dir
    if module_dir.exists():
        raise RecipeError(f"Git module storage already exists: {module_dir}")
    modules = root / ".gitmodules"
    original_modules = modules.read_bytes() if modules.exists() else None
    created_dirs = [path for path in (root / ".agents", target.parent) if not path.exists()]
    # Prepare the pinned checkout before touching the target path.  A valid
    # pinned commit need not be reachable from the remote's default branch.
    with tempfile.TemporaryDirectory(prefix=".recipkg-skill-check-", dir=root) as tmp:
        probe = Path(tmp) / "repo"
        _git(Path(tmp), "clone", "--no-checkout", "--no-recurse-submodules", "--", url, str(probe))
        _git(probe, "fetch", "--no-tags", "origin", rev)
        _validate_at(probe, rev, name)
        _git(probe, "checkout", "--detach", rev)
        target.parent.mkdir(parents=True, exist_ok=True)
        probe.rename(target)
    try:
        _git(root, "submodule", "add", "--name", module, "--", url, rel)
        _git(root, "submodule", "absorbgitdirs", "--", rel)
        _git(root, "add", "--", rel)
    except RecipeError as exc:
        # Remove only our pristine, unregistered checkout. Once Git has
        # changed registration or user content, retain it for manual review.
        try:
            current_modules = modules.read_bytes() if modules.exists() and not modules.is_symlink() else None
            indexed = _git(root, "ls-files", "--stage", "--", rel).stdout
            clean = _git(target, "status", "--porcelain", "--ignored", "--untracked-files=all").stdout
            head = _git(target, "rev-parse", "HEAD").stdout.strip()
            safe = (not target.is_symlink() and (target / ".git").is_dir()
                    and not module_dir.exists() and not indexed and not clean and head == rev
                    and not modules.is_symlink() and current_modules == original_modules)
            if safe:
                shutil.rmtree(target)
                for directory in reversed(created_dirs):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
            else:
                raise RecipeError(f"{exc}; partial skill state retained at {target}") from exc
        except (OSError, RecipeError) as cleanup_error:
            raise RecipeError(f"{exc}; check partial skill state at {target}: {cleanup_error}") from exc
        raise


def update_project_skill(project: Path, name: str, rev: str) -> None:
    root = _root(project)
    target, rel = _paths(root, name)
    _rev(rev)
    entries = _entries(root)
    if name not in entries:
        raise RecipeError(f"Unknown project skill: {name}")
    list_project_skills(root)
    if not target.is_dir() or not (target / ".git").is_file():
        raise RecipeError(f"Skill submodule is not initialized: {rel}")
    if _git(root, "status", "--porcelain", "--", rel).stdout or _git(target, "status", "--porcelain", "--ignored", "--untracked-files=all").stdout:
        raise RecipeError("Skill contains local changes or untracked files")
    if _git(target, "symbolic-ref", "-q", "HEAD", check=False).returncode == 0:
        raise RecipeError("Skill is on a local branch; preserve the branch and detach it explicitly before updating")
    if _git(target, "config", "--local", "--get-regexp", r"^filter\.", check=False).returncode == 0:
        raise RecipeError("Refusing a skill repository with locally configured Git filters")
    actual_url = _git(target, "remote", "get-url", "origin").stdout.strip()
    if actual_url != entries[name][1]:
        raise RecipeError("Skill origin differs from .gitmodules")
    _source(actual_url)
    _git(target, "fetch", "--no-tags", "origin", rev)
    _validate_at(target, rev, name)
    _git(target, "checkout", "--detach", rev)
    _git(root, "add", "--", rel)
