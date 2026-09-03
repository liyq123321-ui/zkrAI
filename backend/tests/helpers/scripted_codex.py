"""Keep gateway validation real while replacing only the external CLI response."""

from collections import deque

from app.agents.codex import CodexAgentGateway, CodexStructuredRunner
from app.config import Settings


def gateway_with_outputs(tmp_path, monkeypatch, outputs):
    settings = Settings(database_url="sqlite://", codex_cwd=tmp_path,
                        codex_binary="unused-codex", codex_home=tmp_path / "codex-home")
    runner = CodexStructuredRunner(settings)
    remaining = deque(outputs)
    prompts = []

    async def run_once(prompt, output_type, cwd):
        prompts.append(prompt)
        output = remaining.popleft()
        if isinstance(output, Exception):
            raise output
        return output if isinstance(output, str) else output.model_dump_json()

    monkeypatch.setattr(runner, "_run_once", run_once)
    return CodexAgentGateway(runner, settings), prompts
