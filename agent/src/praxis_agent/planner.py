"""Turns an intent into a screen, one model step at a time.

Each step the model either asks for a file lookup, proposes a change to a file,
or replies. All are the same schema-constrained output, so a small local model
cannot produce a malformed lookup any more than a malformed reply. A proposed
change is never made here: it ends the turn, and the user decides. Provider function
calling is not used: with a 7B model it either never fired (under constrained output)
or looped.
"""

import os
import time
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
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
from .ui.a2ui import MAX_BLOCKS
from .ui.plan import CardsBlock, CodeBlock, FormBlock, Screen, TextBlock, UIPlan

# Lookups allowed before the model has to reply with what it has.
MAX_LOOKUPS = 3

INSTRUCTIONS = """\
You are Praxis, the assistant built into a Linux desktop. The user types what they
want and you reply. Your reply is shown to them as a title and a list of blocks.
That is only how the reply is displayed, not a topic: do not talk about it.

What you can do right now: answer questions and explain things from your own
knowledge, write code or text for the user to use, look at the user's files, and
propose a change to a text file or a new file, which the user then applies or not.
The user can run a Python, shell or JavaScript file themselves with the Run button
under it; you cannot run anything. What you cannot do yet: move, rename or delete
files; run commands; change settings. When a request needs one of those, say in one
sentence that Praxis cannot do that yet, then offer what you can do.

Each of your answers is one step:
- a lookup: search_files to find files by name, recent_files to find files changed
  lately, list_directory to see a folder, read_text_file to read a file. You are
  then given the result and asked for the next step.
- propose_edit to change a text file or create a new one, when the user asks for
  that: give the file's complete new text, not just the part that changes. Read the
  file first unless it is new. The user is shown the change and applies it or not,
  so never say it is done.
- reply to answer the user.
Look things up before replying whenever the request is to find, show, read or
summarise the user's files or folders. Do not look anything up for general questions,
or for requests to move or delete something: reply that Praxis cannot do that yet.
When the user asks for code without naming a file, reply with a code block.
If the user asks you to run a file, say that the Run button under the file runs it.
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


class ProposeEdit(BaseModel):
    """Change a text file, or create one. The user is shown the change and decides."""

    do: Literal["propose_edit"]
    path: str = Field(description="The file, e.g. 'code/app.py'. A new file if it does not exist.")
    summary: str = Field(description="One sentence: what the change does.")
    content: str = Field(description="The file's complete new text, not just the changed part.")


class Step(BaseModel):
    step: Annotated[
        SearchFiles | RecentFiles | ListDirectory | ReadTextFile | ProposeEdit | UIPlan,
        Field(discriminator="do"),
    ]


class FinalStep(BaseModel):
    """The step once the lookups are used up: only a reply is left."""

    step: UIPlan


RunLookup = Callable[[Lookup], Awaitable[Listing | FileText]]

# How often a reply being written is redrawn, at most.
DRAFT_INTERVAL = 0.1


@dataclass
class Looking:
    """The model asked for a lookup, which is about to run."""

    lookup: Lookup


@dataclass
class Draft:
    """The reply so far, while the model is still writing it."""

    screen: Screen


@dataclass
class Proposing:
    """The model has started writing a change to the file at `path`."""

    path: str


@dataclass
class Turn:
    # When the model proposed a change, a reply made from its summary.
    reply: UIPlan
    # What was looked up on the way, in order.
    found: list[Listing | FileText]
    # Why praxis-fs refused a lookup, for each one it refused.
    refused: list[str]
    # What the model was sent and answered during this turn only.
    messages: list[ModelMessage]
    requests: int
    # The change the model proposed, if it ended the turn with one.
    proposal: ProposeEdit | None = None

    def screen(self) -> Screen:
        return compose(self.reply, self.found, self.refused)


Event = Looking | Draft | Proposing | Turn


def compose(
    reply: UIPlan,
    found: list[Listing | FileText],
    refused: list[str],
    *,
    partial: bool = False,
) -> Screen:
    """The reply with what was looked up placed after the answer, before any cards or form.

    Refusals are stated in the agent's own words as well: the model's account of
    one tends to be that the file does not exist.

    A `partial` reply is still being written. Its last block is left out unless it
    is text or code, which can be shown growing; cards and forms appear once whole.
    """
    blocks = list(reply.blocks)
    if partial and blocks and not isinstance(blocks[-1], TextBlock | CodeBlock):
        blocks.pop()
    shown: list[Listing | FileText | TextBlock] = []
    seen: set[tuple[str | None, ...]] = set()
    for result in found:
        # A search that found nothing has nothing to show; the reply says so.
        if isinstance(result, Listing) and result.pattern and not result.total:
            continue
        # A model that looks at the same thing twice still shows it once.
        key = (result.path,) if isinstance(result, FileText) else (result.directory, result.pattern)
        if key not in seen:
            seen.add(key)
            shown.append(result)
    shown += [
        TextBlock(kind="text", style="caption", text=f"Not read: {reason}")
        for reason in dict.fromkeys(refused)
    ]
    # A screen holds MAX_BLOCKS blocks. What the agent looked up is never what gets cut.
    blocks = blocks[: max(0, MAX_BLOCKS - len(shown))]
    at = next(
        (i for i, block in enumerate(blocks) if isinstance(block, CardsBlock | FormBlock)),
        len(blocks),
    )
    return Screen(
        # A screen about files is in File mode, whatever the model called it.
        mode="file" if shown else reply.mode,
        title=reply.title,
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
) -> AsyncIterator[Event]:
    """Run one turn: let the model look things up with `lookup`, then reply.

    Yields a `Looking` before each lookup, `Draft`s while the reply is being written
    or a `Proposing` once a proposed change is under way, and finally the `Turn`.
    `main_folder` is the folder the model's relative paths start from.
    """
    found: list[Listing | FileText] = []
    refused: list[str] = []
    requests = 0
    start = len(history)
    ask = prompt
    for used in range(MAX_LOOKUPS + 1):
        output_type = Step if used < MAX_LOOKUPS else FinalStep
        # iter() rather than run_stream(): run_stream() cannot retry an invalid reply.
        async with planner.iter(
            ask,
            model=model,
            message_history=history,
            instructions=INSTRUCTIONS,
            output_type=NativeOutput(output_type),
        ) as run:
            async for node in run:
                if Agent.is_model_request_node(node):
                    async with node.stream(run.ctx) as stream:
                        async for so_far in _steps_so_far(stream, output_type):
                            if isinstance(so_far, Proposing):
                                yield so_far
                            else:
                                yield Draft(compose(so_far, found, refused, partial=True))
            result = run.result
        assert result is not None
        requests += result.usage.requests
        history = result.all_messages()
        step = result.output.step
        if isinstance(step, ProposeEdit):
            yield Turn(
                reply=UIPlan(
                    do="reply",
                    mode="code",
                    title=f"Change to {PurePath(step.path).name or step.path}",
                    blocks=[TextBlock(kind="text", text=step.summary or "A proposed change.")],
                ),
                found=found,
                refused=refused,
                messages=history[start:],
                requests=requests,
                proposal=step,
            )
            return
        if isinstance(step, UIPlan):
            yield Turn(
                reply=step,
                found=found,
                refused=refused,
                messages=history[start:],
                requests=requests,
            )
            return

        yield Looking(step)
        try:
            outcome = await lookup(step)
        except ToolFailure as failure:
            # Refusals name the allowed folders; the model writes paths relative to the main one.
            reason = str(failure).replace(main_folder, "the main folder")
            if failure.refused:
                refused.append(str(failure))
                ask = (
                    f"{step.do} was refused: {reason}\n"
                    "The user has been told this. Do not say the file is missing.\n\n"
                    "Next step?"
                )
            else:
                ask = (
                    f"{step.do} failed: {reason}\n"
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


async def _steps_so_far(
    stream: AsyncIterable[Any], output_type: type[Step] | type[FinalStep]
) -> AsyncIterator[UIPlan | Proposing]:
    """The reply as far as the model has written it, as it streams in; or, once, that
    it is writing a proposed change.

    Nothing is yielded for a step that turns out to be a lookup.
    """
    schema: TypeAdapter[Step | FinalStep] = TypeAdapter(output_type)
    text = ""
    last = 0.0
    proposing = False
    async for event in stream:
        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
            text = event.part.content
        elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
            text += event.delta.content_delta
        else:
            continue
        if time.monotonic() - last < DRAFT_INTERVAL:
            continue
        try:
            step = schema.validate_json(text, experimental_allow_partial="trailing-strings").step
        except ValidationError:
            continue
        if isinstance(step, UIPlan):
            last = time.monotonic()
            yield step
        # The path is whole once the summary has begun.
        elif isinstance(step, ProposeEdit) and step.summary and not proposing:
            proposing = True
            yield Proposing(step.path)
