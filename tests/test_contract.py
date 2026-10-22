from __future__ import annotations

import pytest

from morphd import contract
from morphd.contract import ContractError, TaskContract, UserTurn


def test_contract_roundtrip_and_hash(add_task, task_store):
    ref = add_task("demo-1", split="test")
    task = task_store.get(ref)
    assert task.user_turns[0].text == task.instruction
    assert task.content_hash() == TaskContract.from_dict(task.to_dict()).content_hash()
    assert task_store.tasks("test") == [ref]
    assert task_store.tasks("all") == [ref]
    assert task_store.verify_workspace(ref) == task.workspace_sha256


def test_contract_rejects_u1_mismatch(add_task, task_store):
    ref = add_task("demo-1")
    payload = task_store.get(ref).to_dict()
    payload["user_turns"][0]["text"] = "different"
    with pytest.raises(ContractError):
        TaskContract.from_dict(payload)


def test_contract_rejects_escaping_check_path(add_task, task_store):
    ref = add_task("demo-1")
    payload = task_store.get(ref).to_dict()
    payload["criteria"]["checks"] = [{"type": "file_exists", "path": "../secret"}]
    with pytest.raises(ContractError):
        TaskContract.from_dict(payload)


def test_contract_rejects_unknown_fields(add_task, task_store):
    ref = add_task("demo-1")
    payload = task_store.get(ref).to_dict()
    payload["runtime"] = "dsh"
    with pytest.raises(ContractError):
        TaskContract.from_dict(payload)


def test_task_store_is_immutable(add_task, env, workspace):
    ref = add_task("demo-1")
    task = contract.TaskStore(env["settings"].tasks_root).get(ref)
    changed = task.to_dict()
    changed["domain"] = "other"
    from morphd.contract import TaskContract

    with pytest.raises(ContractError):
        contract.TaskStore(env["settings"].tasks_root).add_task(
            TaskContract.from_dict(changed), env["tmp"] / "staged-demo-1"
        )


def test_hidden_terms_only_high_signal(add_task, task_store):
    turns = [UserTurn("FAIL_FIRST: do it"), UserTurn("please fix it", corrective=True)]
    ref = add_task(
        "hidden-1",
        instruction=turns[0].text,
        turns=turns,
        checks=[
            {"type": "file_contains", "path": "hello.txt", "text": "hello"},
            {"type": "file_contains", "path": "hello.txt", "text": "answer-42", "hidden": True},
            {"type": "file_sha256", "path": "x", "sha256": "a" * 64},
            {"type": "json_equals", "path": "x.json", "pointer": "/k", "value": "secret"},
        ],
    )
    terms = task_store.get(ref).hidden_terms()
    assert "hello" not in terms
    assert "answer-42" in terms
    assert "a" * 64 in terms
    assert "secret" in terms


def test_split_must_cover_every_task(add_task, task_store):
    add_task("one", split="test")
    add_task("two", split="test")
    add_task("three", instruction="write hello 3", domain="demo")
    with pytest.raises(ContractError, match="does not cover"):
        task_store.tasks("test")
    task_store.write_splits(
        {task: "test" for task in task_store.all()}, force=True
    )
    assert len(task_store.tasks("test")) == 3


def test_split_assignment_counts(add_task, task_store):
    for i in range(10):
        add_task(f"t{i}", instruction=f"write hello {i}", domain="demo")
    assignments = task_store.build_split_assignments(holdout=0.2, test=0.5, seed=4)
    assert set(assignments) == set(task_store.all())
    counts = {name: list(assignments.values()).count(name) for name in set(assignments.values())}
    assert counts["holdout"] == 2
    path = task_store.write_splits(assignments)
    assert path.is_file()
    assert task_store.tasks("holdout")
    with pytest.raises(ContractError):
        task_store.write_splits(assignments)
