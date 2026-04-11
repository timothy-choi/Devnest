"""Minimal subprocess wrapper for topology's Linux/system commands.

Topology implementation code should use this runner instead of calling ``subprocess`` directly,
so command execution stays mockable and error messages stay consistent.
"""

from __future__ import annotations

import shlex
import subprocess


class CommandRunner:
    def run(self, cmd: list[str]) -> str:
        """
        Executes a system command and returns stdout as string.
        Raises RuntimeError on failure.
        """
        if not cmd:
            raise ValueError("cmd must be a non-empty list of strings")

        try:
            cp = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            stdout = (e.stdout or "").strip()
            stderr = (e.stderr or "").strip()
            pretty = shlex.join([str(x) for x in cmd])
            raise RuntimeError(
                f"command failed (exit={e.returncode}): {pretty}\n"
                f"stdout: {stdout!r}\n"
                f"stderr: {stderr!r}"
            ) from e
        except subprocess.TimeoutExpired as e:
            pretty = shlex.join([str(x) for x in cmd])
            raise RuntimeError(f"command timed out after {e.timeout}s: {pretty}") from e

        return (cp.stdout or "")

