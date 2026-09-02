from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent.parent


SYSTEM_PROMPT_FILE = (
    BASE_DIR /
    "prompts" /
    "system.txt"
)


def build_prompt(user_message):

    system = SYSTEM_PROMPT_FILE.read_text(
        encoding="utf-8"
    )


    return f"""
{system}


User Request:

{user_message}

"""
