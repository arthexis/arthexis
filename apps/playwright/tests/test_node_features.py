from __future__ import annotations

from apps.playwright import node_features


def test_windows_event_loop_policy_is_set_for_playwright(monkeypatch):
    class OtherPolicy:
        pass

    class ProactorPolicy:
        pass

    state = {"policy": OtherPolicy()}

    monkeypatch.setattr(node_features.sys, "platform", "win32")
    monkeypatch.setattr(
        node_features.asyncio,
        "WindowsProactorEventLoopPolicy",
        ProactorPolicy,
        raising=False,
    )
    monkeypatch.setattr(
        node_features.asyncio,
        "get_event_loop_policy",
        lambda: state["policy"],
    )
    monkeypatch.setattr(
        node_features.asyncio,
        "set_event_loop_policy",
        lambda policy: state.update(policy=policy),
    )

    node_features._ensure_windows_subprocess_event_loop_policy()

    assert isinstance(state["policy"], ProactorPolicy)


def test_windows_event_loop_policy_keeps_existing_proactor(monkeypatch):
    class ProactorPolicy:
        pass

    state = {"policy": ProactorPolicy(), "set_calls": 0}

    monkeypatch.setattr(node_features.sys, "platform", "win32")
    monkeypatch.setattr(
        node_features.asyncio,
        "WindowsProactorEventLoopPolicy",
        ProactorPolicy,
        raising=False,
    )
    monkeypatch.setattr(
        node_features.asyncio,
        "get_event_loop_policy",
        lambda: state["policy"],
    )
    monkeypatch.setattr(
        node_features.asyncio,
        "set_event_loop_policy",
        lambda policy: state.update(policy=policy, set_calls=state["set_calls"] + 1),
    )

    node_features._ensure_windows_subprocess_event_loop_policy()

    assert state["set_calls"] == 0
