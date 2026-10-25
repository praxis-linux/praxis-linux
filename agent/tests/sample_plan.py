"""A reply and a screen using every block type, shared by the builder and server tests."""

from datetime import UTC, datetime

from praxis_agent.tools.files import Entry, FileText, ImageFile, Listing
from praxis_agent.ui.plan import (
    Card,
    CardsBlock,
    CodeBlock,
    FormBlock,
    FormField,
    Screen,
    TextBlock,
    UIPlan,
)

SAMPLE_REPLY = UIPlan(
    do="reply",
    mode="conversation",
    title="Plan a project",
    blocks=[
        TextBlock(kind="text", text="Tell me about the **project**.", style="body"),
        FormBlock(
            kind="form",
            purpose="Gather project details",
            fields=[
                FormField(name="Project Name", label="Project name"),
                FormField(name="goal", label="Goal", multiline=True),
            ],
            submit_label="Continue",
        ),
        CardsBlock(
            kind="cards",
            items=[
                Card(
                    title="See an example",
                    description="A filled-in plan",
                    intent="Show me an example project plan",
                ),
                Card(title="Start over", intent="Start a new conversation"),
            ],
        ),
        CodeBlock(kind="code", language="python", code='print("hello")\nprint("world")'),
    ],
)

SAMPLE_LISTING = Listing(
    directory="/home/ada/projects",
    entries=[
        Entry(
            name="praxis",
            path="/home/ada/projects/praxis",
            kind="directory",
            size=None,
            modified=datetime(2026, 9, 28, 9, 30, tzinfo=UTC),
        ),
        Entry(
            name="__init__.py",
            path="/home/ada/projects/__init__.py",
            kind="file",
            size=2048,
            modified=datetime(2026, 9, 29, 14, 5, tzinfo=UTC),
        ),
    ],
    total=2,
    truncated=False,
)

SAMPLE_FILE = FileText(
    path="/home/ada/projects/notes.md",
    size=27,
    content="# Notes\n\n- *ship* the demo\n",
    truncated=False,
)

# One transparent pixel.
PIXEL_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

SAMPLE_IMAGE = ImageFile(
    path="/home/ada/projects/pixel.png", size=70, media_type="image/png", data=PIXEL_PNG
)

# Every block type, as the Canvas tests need it: written to the golden file.
SAMPLE_SCREEN = Screen(
    mode=SAMPLE_REPLY.mode,
    title=SAMPLE_REPLY.title,
    blocks=[*SAMPLE_REPLY.blocks, SAMPLE_LISTING, SAMPLE_FILE, SAMPLE_IMAGE],
    up="/home/ada",
)
