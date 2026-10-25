from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from praxis_agent.memory import REMEMBERED_TURNS, Conversations

SCREEN = [{"version": "v0.9", "deleteSurface": {"surfaceId": "workspace"}}]


def turn(prompt, *steps):
    """A turn's messages: the request, a response and follow-up per lookup, then the reply."""
    messages = [ModelRequest(parts=[UserPromptPart(prompt)])]
    for lookup, result in steps:
        messages.append(ModelResponse(parts=[TextPart(lookup)]))
        messages.append(ModelRequest(parts=[UserPromptPart(result)]))
    messages.append(ModelResponse(parts=[TextPart(f"reply to {prompt}")]))
    return messages


def record(store, session, messages, label="q", on_screen=None):
    return store.record(
        session,
        label=label,
        title=f"title {label}",
        mode="conversation",
        messages=messages,
        screen=SCREEN,
        on_screen=on_screen,
    )


def texts(messages):
    return [part.content for message in messages for part in message.parts]


def test_turns_are_numbered_per_conversation(tmp_path):
    store = Conversations(tmp_path / "c.db")

    assert record(store, "a", turn("one")) == 1
    assert record(store, "a", turn("two")) == 2
    assert record(store, "b", turn("other")) == 1
    assert [t.n for t in store.turns("a")] == [1, 2]


def test_the_latest_turn_is_remembered_in_full_and_earlier_ones_compactly(tmp_path):
    store = Conversations(tmp_path / "c.db")
    record(store, "s", turn("find report", ("search report", "SECRET CONTENTS OF report.md")))
    record(store, "s", turn("open plan", ("read plan", "contents of plan.md")))

    assert texts(store.history("s")) == [
        "find report",
        "reply to find report",
        # What was read in an earlier turn is not sent again.
        "open plan",
        "read plan",
        "contents of plan.md",
        "reply to open plan",
    ]


def test_only_the_last_turns_are_remembered(tmp_path):
    store = Conversations(tmp_path / "c.db")
    for i in range(REMEMBERED_TURNS + 3):
        record(store, "s", turn(f"question {i}"))

    remembered = texts(store.history("s"))

    assert remembered[0] == "question 3"
    assert remembered[-1] == f"reply to question {REMEMBERED_TURNS + 2}"
    assert len(remembered) == 2 * REMEMBERED_TURNS
    # All of them stay visible in the trail.
    assert len(store.turns("s")) == REMEMBERED_TURNS + 3


def test_a_stored_screen_comes_back_as_it_was(tmp_path):
    store = Conversations(tmp_path / "c.db")
    record(store, "s", turn("open plan"), on_screen="the file docs/plan.md")

    stored = store.screen("s", 1)

    assert stored is not None
    assert (stored.mode, stored.messages, stored.on_screen) == (
        "conversation",
        SCREEN,
        "the file docs/plan.md",
    )
    assert store.screen("s", 2) is None
    assert store.screen("other", 1) is None


def test_turns_recorded_at_the_same_time_get_distinct_numbers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "c.db"
    Conversations(path)

    def write(i):
        return record(Conversations(path), "s", turn(f"question {i}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = list(pool.map(write, range(8)))

    assert sorted(numbers) == list(range(1, 9))


def test_a_new_conversation_has_no_history(tmp_path):
    store = Conversations(tmp_path / "c.db")

    assert store.history("new") == []
    assert store.turns("new") == []
