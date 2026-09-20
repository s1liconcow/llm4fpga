"""Run trusted tool commands in a networkless Podman container."""
from __future__ import annotations

import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Toolchain:
    runtime: str = 'podman'
    image: str = 'xls-e2e-tools:local'
    platform: str | None = None

    def command(self, command: list[str], folder: Path, name: str, *, interactive: bool = False) -> list[str]:
        """Shared isolation policy for batch tools and persistent simulator sessions."""
        args = [self.runtime, 'run', '--rm', '--pull=never', '--name', name, '--network=none',
                '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                '--pids-limit=128', f'--memory={os.getenv("FPGA_LAB_MEMORY", "1536m")}', '--cpus=4',
                '--sysctl=net.ipv4.ping_group_range=0 0',
                '--tmpfs=/tmp:rw,size=256m',
                '-v', f'{folder.resolve()}:/work:rw', '-w', '/work', '-e', 'HOME=/tmp']
        if interactive:
            args += ['-i']
        if self.platform:
            args += ['--platform', self.platform]
        return [*args, self.image, *command]

    def run(self, command: list[str], folder: Path, *, timeout: int = 180,
            log: str = 'tool.log') -> str:
        folder = folder.resolve()
        folder.mkdir(parents=True, exist_ok=True)
        name = 'fpga-lab-' + uuid.uuid4().hex[:16]
        args = self.command(command, folder, name)
        log_path = folder / log
        # Do not pass host environment variables into the container.
        try:
            with log_path.open('w') as stream:
                result = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT,
                                        timeout=timeout, check=False)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            subprocess.run([self.runtime, 'rm', '-f', name], capture_output=True, timeout=20)
            raise
        text = log_path.read_text(errors='replace')
        if result.returncode:
            raise RuntimeError(f'{command[0]} failed ({result.returncode}):\n{text[-7000:]}')
        return text

    @classmethod
    def configured(cls) -> 'Toolchain':
        return cls(os.getenv('FPGA_LAB_RUNTIME', 'podman'),
                   os.getenv('FPGA_LAB_IMAGE', 'xls-e2e-tools:local'))
