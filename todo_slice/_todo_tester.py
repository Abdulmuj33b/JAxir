#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import todo

store = os.environ.get("TODO_STORE", "test_todos.json")
# monkeypatch the store path for testing
import json, os as _os
_todo_store = store

def _load():
    if _os.path.exists(_todo_store):
        with open(_todo_store) as f:
            return json.load(f)
    return []

def _save(todos):
    with open(_todo_store, "w") as f:
        json.dump(todos, f, indent=2)

todo.load = _load
todo.save = _save

# re-execute the cmd_* dispatch for the requested action
import argparse
p = argparse.ArgumentParser()
sub = p.add_subparsers(dest="cmd", required=True)
ap = sub.add_parser("__call__")
ap.add_argument("action")
ap.add_argument("payload", nargs="*")
ap.set_defaults(func=None)
args = p.parse_args()
