from pathlib import Path



BASE_DIR = Path(__file__).resolve().parents[2]


PROMPT_DIR = BASE_DIR / "prompts"



def load_prompt(filename):

    path = PROMPT_DIR / filename

    if not path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {path}"
        )

    return path.read_text(
        encoding="utf-8"
    )


def build_prompt(
    history,
    message,
    workflow,
    agent,
    workflow_state=None,
):
    system_prompt = load_prompt(
        "system.txt"
    )


    workflow_prompt = load_prompt(
        f"workflows/{workflow}.txt"
    )


    agent_prompt = load_prompt(
        "agent_rules.txt"
    )


    conversation = ""


    for item in history:

        conversation += (
            f"{item['role']}: "
            f"{item['content']}\n"
        )


    prompt = f"""
{system_prompt}


## Agent

{agent_prompt}


## Workflow

{workflow_prompt}


## Conversation History

{conversation}


## Current User Request

{message}

"""


    return prompt
