import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from pydantic import SecretStr


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    model_name: str
    raw_response: dict = field(default_factory=dict)


def run_model(model: str, prompt: str, source_dir: Path, config: dict) -> RunResult:
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        raise RuntimeError("LLM_API_KEY environment variable is not set")

    llm = LLM(
        model=model,
        api_key=SecretStr(api_key),
    )

    tools = [
        Tool(name=FileEditorTool.name),
        Tool(name=TerminalTool.name),
    ]

    agent = Agent(llm=llm, tools=tools)

    conversation = Conversation(
        agent=agent,
        workspace=str(source_dir),
        max_iteration_per_run=config.get("max_iterations", 10),
    )

    start = time.monotonic()
    try:
        conversation.send_message(prompt)
        conversation.run()
        duration = time.monotonic() - start

        response = get_agent_final_response(conversation.state.events) or ""
        cost = llm.metrics.accumulated_cost

        return RunResult(
            stdout=response,
            stderr="",
            exit_code=0,
            duration_seconds=duration,
            model_name=model,
            raw_response={"cost": cost},
        )
    except Exception as e:
        duration = time.monotonic() - start
        return RunResult(
            stdout="",
            stderr=str(e),
            exit_code=1,
            duration_seconds=duration,
            model_name=model,
        )
