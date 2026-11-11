"""Google Tasks: task lists, and tasks with due dates and subtasks."""

from datetime import datetime

from . import shape
from .api import TASKS, quote
from .errors import NOT_FOUND, GcalError, approval, bad_input
from .times import parse_moment
from .untrusted import clean_line

DEFAULT_LIST = "@default"


class Tasks:
    def __init__(self, api, account):
        self.api, self.account = api, account
        self._lists = None
        self._default = None

    # Task lists

    def lists(self):
        if self._lists is None:
            self._lists = self.api.pages(f"{TASKS}/users/@me/lists", maxResults=100)
        return self._lists

    def show_lists(self):
        return [{"id": item["id"], "name": clean_line(item.get("title")), "updated": item.get("updated")} for item in self.lists()]

    def resolve(self, name_or_id):
        if not name_or_id or name_or_id == DEFAULT_LIST:
            if self._default is None:
                self._default = self.api.get(f"{TASKS}/users/@me/lists/{DEFAULT_LIST}")["id"]
            return self._default
        for item in self.lists():
            if item["id"] == name_or_id:
                return item["id"]
        found = [item for item in self.lists() if (item.get("title") or "").casefold() == name_or_id.casefold()]
        if len(found) == 1:
            return found[0]["id"]
        candidates = [{"id": item["id"], "name": clean_line(item.get("title"))} for item in (found or self.lists())]
        message = f"more than one task list is named {name_or_id}" if found else f"no task list fits {name_or_id}"
        raise GcalError(NOT_FOUND, message, "use an id from candidates", candidates)

    def list_name(self, list_id):
        return next((clean_line(item.get("title")) for item in self.lists() if item["id"] == list_id), list_id)

    def create_list(self, name):
        created = self.api.request("POST", f"{TASKS}/users/@me/lists", body={"title": name})
        self._lists = None
        return {"id": created["id"], "name": clean_line(created.get("title"))}

    def rename_list(self, name_or_id, name):
        list_id = self.resolve(name_or_id)
        updated = self.api.request("PATCH", f"{TASKS}/users/@me/lists/{quote(list_id)}", body={"title": name})
        self._lists = None
        return {"id": updated["id"], "name": clean_line(updated.get("title"))}

    def delete_list(self, name_or_id, yes):
        list_id = self.resolve(name_or_id)
        if list_id == self.resolve(None):
            raise bad_input("Google does not allow deleting the default task list")
        if not yes:
            raise approval(f"this deletes the list {self.list_name(list_id)} and all its tasks", "--yes")
        self.api.request("DELETE", f"{TASKS}/users/@me/lists/{quote(list_id)}")
        self._lists = None
        return {"deleted": list_id}

    # Tasks

    def tasks(self, list_name=None, done=False, due_before=None, body=False):
        list_id = self.resolve(list_name)
        params = {
            "showCompleted": "true" if done else "false",
            "showHidden": "true" if done else "false",
            "showAssigned": "true",
            "maxResults": 100,
        }
        if due_before:
            params["dueMax"] = _due(due_before, self.account.tz)
        items = self.api.pages(self._url(list_id), **params)
        return [self._shape(item, list_id, body) for item in _outline(items)]

    def get(self, ref, body=False):
        list_id, item = self._load(ref)
        return self._shape(item, list_id, body)

    def add(self, title, list_name=None, notes=None, due=None, parent=None, after=None):
        list_id = self.resolve(list_name)
        body = {"title": title, "notes": notes, "due": _due(due, self.account.tz) if due else None}
        params = self._place(parent, after, list_id)
        created = self.api.request("POST", self._url(list_id), params, {k: v for k, v in body.items() if v is not None})
        return self._shape(created, list_id)

    def edit(self, ref, title=None, notes=None, due=None):
        list_id, item = self._load(ref)
        body = {}
        if title is not None:
            body["title"] = title
        if notes is not None:
            body["notes"] = notes
        if due is not None:
            body["due"] = None if due == "none" else _due(due, self.account.tz)
        if not body:
            raise bad_input("nothing to change", "add --title, --notes, or --due")
        return self._patch(list_id, item, body)

    def complete(self, ref):
        list_id, item = self._load(ref)
        return self._patch(list_id, item, {"status": "completed"})

    def reopen(self, ref):
        list_id, item = self._load(ref)
        return self._patch(list_id, item, {"status": "needsAction", "completed": None})

    def move(self, ref, parent=None, after=None, to_list=None, top=False):
        list_id, item = self._load(ref)
        destination = self.resolve(to_list) if to_list else None
        params = {
            **self._place(parent, after, destination or list_id),
            "destinationTasklist": destination if destination and destination != list_id else None,
        }
        if not (top or any(params.values())):
            raise bad_input("say where to move the task", "add --parent, --after, --top, or --list")
        moved = self.api.request("POST", f"{self._url(list_id, item['id'])}/move", params)
        return self._shape(moved, destination or list_id)

    def delete(self, ref, yes):
        list_id, item = self._load(ref)
        if not yes:
            # The title of an assigned task comes from another person, and errors have no untrusted wrapper.
            raise approval(f"this deletes the task {shape.ref(list_id, item['id'])} and its subtasks", "--yes")
        self.api.request("DELETE", self._url(list_id, item["id"]))
        return {"deleted": shape.ref(list_id, item["id"])}

    # Helpers

    def _url(self, list_id, task_id=None):
        url = f"{TASKS}/lists/{quote(list_id)}/tasks"
        return f"{url}/{quote(task_id)}" if task_id else url

    def _load(self, ref):
        list_part, task_id = shape.split_ref(ref, DEFAULT_LIST)
        list_id = self.resolve(list_part)
        return list_id, self.api.get(self._url(list_id, task_id))

    def _child_id(self, ref, list_id):
        if not ref:
            return None
        ref_list, task_id = shape.split_ref(ref, list_id)
        if self.resolve(ref_list) != list_id:
            raise bad_input("--parent and --after must be tasks in the same list")
        return task_id

    def _place(self, parent, after, list_id):
        previous = self._child_id(after, list_id)
        parent_id = self._child_id(parent, list_id)
        # The Tasks app shows one level of subtasks only.
        if parent_id and self.api.get(self._url(list_id, parent_id)).get("parent"):
            raise bad_input("--parent must be a top-level task", "use the parent of that task")
        if previous and not parent:
            # Google puts a task with no parent at the top level, so "after a subtask" needs the parent of that subtask.
            parent_id = self.api.get(self._url(list_id, previous)).get("parent")
        return {"parent": parent_id, "previous": previous}

    def _patch(self, list_id, item, body):
        updated = self.api.request("PATCH", self._url(list_id, item["id"]), body=body)
        return self._shape(updated, list_id)

    def _shape(self, item, list_id, body=False):
        return shape.task(item, list_id, self.list_name(list_id), self.account.tz, body)


def _outline(items):
    """Top-level tasks in their list order, each one followed by its subtasks."""
    by_position = sorted(items, key=lambda item: item.get("position", ""))
    ids = {item["id"] for item in items}
    children = {}
    for item in by_position:
        if item.get("parent") in ids:
            children.setdefault(item["parent"], []).append(item)
    ordered = []
    for item in by_position:
        if item.get("parent") not in ids:
            ordered += [item, *children.get(item["id"], [])]
    return ordered


def _due(text, tz):
    moment = parse_moment(text, tz)
    day = moment.date() if isinstance(moment, datetime) else moment
    # The Tasks API keeps only the date of "due"; the time part must be zero.
    return f"{day.isoformat()}T00:00:00.000Z"
