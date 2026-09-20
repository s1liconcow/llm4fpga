"""Host-side Codex CLI provider. Model output never supplies harness code."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .vhdl import Proposal

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {'vhdl': {'type': 'string'}, 'latency': {'type': 'integer'}, 'notes': {'type': 'string'}},
    'required': ['vhdl', 'latency', 'notes'],
}


class CodexProvider:
    def __init__(self, model: str | None = None, timeout: int = 900):
        self.model = model or os.getenv('CODEX_MODEL')
        if self.model is None:
            # Keep the user's chosen model while excluding unrelated MCP servers/hooks.
            import tomllib
            config = Path(os.getenv('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
            if config.exists():
                self.model = tomllib.loads(config.read_text()).get('model')
        self.timeout = timeout
        if not shutil.which('codex'):
            raise RuntimeError('Install Codex CLI and run codex login first')

    def propose(self, prompt: str, folder: Path) -> Proposal:
        return Proposal.parse(json.dumps(self.request(prompt, folder, SCHEMA)))

    def request(self, prompt: str, folder: Path, schema_definition: dict) -> dict:
        """Request a structured artifact; the model cannot execute or alter the evaluator."""
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'prompt.txt').write_text(prompt)
        with tempfile.TemporaryDirectory(prefix='fpga-lab-codex-') as tmp:
            work = Path(tmp)
            schema = work / 'schema.json'
            schema.write_text(json.dumps(schema_definition))
            output = work / 'response.json'
            command = ['codex', '-a', 'never', 'exec', '--ignore-user-config', '--ephemeral',
                       '--sandbox', 'read-only', '--skip-git-repo-check',
                       '-c', 'features.shell_tool=false', '-c', 'features.multi_agent=false',
                       '--output-schema', str(schema), '--output-last-message', str(output),
                       '--color', 'never', '-C', str(work)]
            if self.model:
                command += ['--model', self.model]
            command += ['-']
            with (folder / 'codex.log').open('w') as stream:
                run = subprocess.run(command, input=prompt, text=True, stdout=stream,
                                     stderr=subprocess.STDOUT, timeout=self.timeout, check=False)
            if run.returncode or not output.exists():
                tail = (folder / 'codex.log').read_text(errors='replace')[-4000:]
                raise RuntimeError(f'Codex failed ({run.returncode}): {tail}')
            response = output.read_text()
            (folder / 'response.json').write_text(response)
            return json.loads(response)
