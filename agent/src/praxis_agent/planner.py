"""Turns an intent into a screen, one model step at a time.

Each step the model either asks for a file lookup or replies. Both are the same
schema-constrained output, so a small local model cannot produce a malformed
lookup any more than a malformed reply. Provider function calling is not used:
with a 7B model it either never fired (under constrained output) or looped.
"""

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.output import NativeOutput
from pydantic_ai.providers.litellm import LiteLLMProvider

from .tools.files import (
    FileText,
    ListDirectory,
    Listing,
    Lookup,
    ReadTextFile,
    RecentFiles,
    SearchFiles,
    ToolFailure,
    describe,
)
from .ui.plan import CardsBlock, FormBlock, Screen, TextBlock, UIPlan

# Lookups allowed before the model has to reply with what it has.
MAX_LOOKUPS = 3

INSTRUCTIONS = """\
You are Praxis, the assistant built into a Linux desktop. The user types what they
want and you reply. Your reply is shown to them as a title and a list of blocks.
That is only how the reply is displayed, not a topic: do not talk about it.

What you can do right now: answer questions and explain things from your own
knowledge, write code or text for the user to use, and look at the user's files.
What you cannot do yet: change, move or delete files; run code or commands; change
settings. When a request needs one of those, say in one sentence that Praxis cannot
do that yet, then offer what you can do.

Each of your answers is one step:
- a lookup: search_files to find files by name, recent_files to find files changed
  lately, list_directory to see a folder, read_text_file to read a file. You are
  then given the result and asked for the next step.
- reply to answer the user.
Look things up before replying whenever the request is to find, show, read or
summarise the user's files or folders. Do not look anything up for general questions,
or for requests to change, move or delete something: reply that Praxis cannot do
that yet.
All paths are relative to the user's main folder: 'Documents' is their Documents
folder, 'Documents/notes.txt' a file in it, and an empty path is the main folder itself.
There is no 'recent' folder: recent_files is how to find what was changed lately.
What you look up is shown to the user automatically, as a list of files or as the
file's text. So in your reply do not repeat file names or file contents: say in a
sentence or two what was found. Only mention files you have actually looked up.

Choose the mode that fits the request:
- conversation: questions, explanations, general chat.
- file: anything about files, folders or documents on the machine.
- code: writing, reading or running code.

Blocks:
- text: your answer in prose. Body text may use Markdown. Keep related paragraphs
  and lists together in one text block.
- code: any code or command, complete, in one block. Never put code in a text block.
- cards: two to four follow-up requests the user can click. Each card's intent is the
  request it would send you, written as the user would say it. Only offer requests
  you can fulfil.
- form: when you need information from the user, ask for it with a form, one field
  per question, instead of listing the questions in text.

Answer the request directly in text first; add cards after the answer when there
are natural follow-ups.
"""


class Step(BaseModel):
    step: Annotated[
        SearchFiles | RecentFiles | ListDirectory | ReadTextFile | UIPlan,
        Field(discriminator="do"),
    ]


class FinalStep(BaseModel):
    """The step once the lookups are used up: only a reply is left."""

    step: UIPlan


RunLookup = Callable[[Lookup], Awaitable[Listing | FileText]]


@dataclass
class Turn:
    reply: UIPlan
    # What was looked up on the way, in order.
    found: list[Listing | FileText]
    # Why praxis-fs refused a lookup, for each one it refused.
    refused: list[str]
    messages: list[ModelMessage]
    requests: int

    def screen(self) -> Screen:
        """The reply with what was looked up placed after the answer, before any cards or form.

        Refusals are stated in the agent's own words as well: the model's account of
        one tends to be that the file does not exist.
        """
        blocks = list(self.reply.blocks)
        # A search that found nothing has nothing to show; the reply says so.
        shown: list[Listing | FileText | TextBlock] = [
            r for r in self.found if not (isinstance(r, Listing) and r.pattern and not r.total)
        ]
        shown += [
            TextBlock(kind="text", style="caption", text=f"Not read: {reason}")
            for reason in dict.fromkeys(self.refused)
        ]
        at = next(
            (i for i, block in enumerate(blocks) if isinstance(block, CardsBlock | FormBlock)),
            len(blocks),
        )
        return Screen(
            # A screen about files is in File mode, whatever the model called it.
            mode="file" if shown else self.reply.mode,
            title=self.reply.title,
            blocks=[*blocks[:at], *shown, *blocks[at:]],
        )


# Schema-constrained JSON output rather than a tool call. With a 7B local model this
# was reliably valid where tool-call output was not (see ADR-003).
planner: Agent[None, Step] = Agent(output_type=NativeOutput(Step), retries=2)


def litellm_model(alias: str) -> Model:
    """The model behind `alias` on the LiteLLM proxy. The agent never calls a provider directly."""
    provider = LiteLLMProvider(
        api_base=os.environ.get("PRAXIS_LITELLM_URL") or "http://127.0.0.1:4000",
        # The proxy only checks the key when LITELLM_MASTER_KEY is set on its side.
        api_key=os.environ.get("LITELLM_MASTER_KEY") or "praxis-local",
    )
    return OpenAIChatModel(alias, provider=provider)


async def plan(
    prompt: str,
    *,
    model: Model,
    history: list[ModelMessage],
    lookup: RunLookup,
    main_folder: str,
) -> Turn:
    """Run one turn: let the model look things up with `lookup`, then reply.

    `main_folder` is the folder the model's relative paths start from.
    """
    found: list[Listing | FileText] = []
    refused: list[str] = []
    requests = 0
    ask = prompt
    for used in range(MAX_LOOKUPS + 1):
        result = await planner.run(
            ask,
            model=model,
            message_history=history,
            instructions=INSTRUCTIONS,
            output_type=NativeOutput(Step if used < MAX_LOOKUPS else FinalStep),
        )
        requests += result.usage.requests
        history = result.all_messages()
        step = result.output.step
        if isinstance(step, UIPlan):
            return Turn(
                reply=step, found=found, refused=refused, messages=history, requests=requests
            )
        try:
            outcome = await lookup(step)
        except ToolFailure as failure:
            if failure.refused:
                refused.append(str(failure))
                ask = (
                    f"{step.do} was refused: {failure}\n"
                    "The user has been told this. Do not say the file is missing.\n\n"
                    "Next step?"
                )
            else:
                ask = (
                    f"{step.do} failed: {failure}\n"
                    "If you are not sure of a path, search_files finds a file by name.\n\n"
                    "Next step?"
                )
        else:
            found.append(outcome)
            ask = (
                f"Result of {step.do}:\n{describe(outcome, main_folder)}\n\n"
                "The user can already see this. Next step?"
            )
    raise AssertionError("the final step can only be a reply")
