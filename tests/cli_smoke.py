"""Run read-only CLI smoke checks against an isolated temporary installation."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_cli(args: list[str], env: dict[str, str], expected: str | None = None) -> str:
    command = [str(ROOT / "bin" / "skillsync"), *args]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"{command!r} exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    if expected and expected not in result.stdout:
        raise AssertionError(f"{command!r} did not print {expected!r}: {result.stdout!r}")
    print(f"PASS bin/skillsync {' '.join(args)}")
    return result.stdout


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="skillsync-cli-smoke-") as tmp:
        isolated_home = Path(tmp)
        registry = isolated_home / "registry"
        platform_skills = isolated_home / "platform" / "skills"
        registry.mkdir()
        (isolated_home / "skills").mkdir()
        platform_skills.mkdir(parents=True)
        shutil.copy2(
            ROOT / "registry" / "catalog.example.yaml",
            registry / "catalog.example.yaml",
        )
        platform_config = isolated_home / "platforms.yaml"
        platform_config.write_text(
            "version: 1\n"
            "platforms:\n"
            "  isolated:\n"
            "    label: CI isolated platform\n"
            f"    skills_dir: {platform_skills}\n",
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["SKILLSYNC_HOME"] = str(isolated_home)
        env["SKILLSYNC_PLATFORMS"] = str(platform_config)
        env["PYTHONPATH"] = str(ROOT / "tools" / "skillsync")

        run_cli(["doctor", "--strict"], env)
        run_cli(["verify"], env, expected="全部通过")
        run_cli(["apply"], env, expected="预演（dry-run")
        help_text = run_cli(["--help"], env, expected="usage: skillsync")
        if "doctor" not in help_text or "apply" not in help_text:
            raise AssertionError("CLI help is missing expected subcommands")
        version = run_cli(["--version"], env, expected="skillsync ")
        if not version.strip().startswith("skillsync "):
            raise AssertionError(f"unexpected CLI version output: {version!r}")

        catalog = registry / "catalog.yaml"
        if not catalog.is_file():
            raise AssertionError("CLI did not initialize the private catalog in SKILLSYNC_HOME")
        if list(platform_skills.iterdir()):
            raise AssertionError("dry-run unexpectedly changed the isolated platform directory")

    print("PASS isolated catalog initialization and apply dry-run left platform directory unchanged")


if __name__ == "__main__":
    main()
