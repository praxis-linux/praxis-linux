"""The UI plan: what the model emits.

The model never writes A2UI directly. It fills in this small nested schema, and
`a2ui.build_messages` compiles it into protocol messages. See ADR-003.
"""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ..tools.files import FileText, Listing

Mode = Literal["conversation", "file", "code"]


class TextBlock(BaseModel):
    """Prose shown to the user. Body text may use Markdown."""

    kind: Literal["text"]
    text: str
    style: Literal["heading", "body", "caption"] = "body"


class CodeBlock(BaseModel):
    """Code for the user to read or copy. One complete piece of code per block."""

    kind: Literal["code"]
    language: str = Field(default="", description="For example 'python' or 'bash'.")
    code: str


class Card(BaseModel):
    title: str
    description: str | None = None
    intent: str = Field(
        description="The request to run if the user picks this card, phrased in their voice."
    )


class CardsBlock(BaseModel):
    """A set of clickable next steps."""

    kind: Literal["cards"]
    items: list[Card]


class FormField(BaseModel):
    name: str = Field(description="Short identifier, e.g. 'project_name'.")
    label: str
    multiline: bool = False


class FormBlock(BaseModel):
    """Asks the user for input. Use only when the request cannot proceed without it."""

    kind: Literal["form"]
    purpose: str = Field(description="One sentence: what submitting this form is for.")
    fields: list[FormField]
    submit_label: str = "Submit"


Block = Annotated[TextBlock | CodeBlock | CardsBlock | FormBlock, Field(discriminator="kind")]


class UIPlan(BaseModel):
    """Reply to the user. Shown on the Canvas as a title and a list of blocks."""

    # First, so the model commits to replying before it writes the reply.
    do: Literal["reply"]
    mode: Mode = Field(description="Which mode best fits the request.")
    title: str = Field(description="Short title for the reply.")
    # An empty reply fails validation, which makes the planner ask the model again.
    blocks: list[Block] = Field(min_length=1)


@dataclass
class Screen:
    """What the Canvas is asked to show: the model's blocks plus what the agent looked up.

    Listings and file contents come straight from tool results, never from the
    model's retelling of them.
    """

    mode: Mode
    title: str
    blocks: list[TextBlock | CodeBlock | CardsBlock | FormBlock | Listing | FileText]
    # The folder above the one this screen lists, when the user can go there.
    up: str | None = None
