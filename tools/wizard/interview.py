"""The WIZARD interview: plain questions in, a profile and a team card out.

``questions.json`` beside this file is the one source of the questions. A
person answers them in the terminal (``wizard.py interview --target DIR``); an
agent asks them in chat, saves the answers as JSON and passes ``--answers``.
Either way the result is the same three files in the project:

* ``profile/owner.md``: who the owner is and the rules the team follows.
  ``profile/**`` is a protected path, so agents can read it but never edit it.
* ``profile/answers.json``: the raw answers, so the interview can be redone.
* ``TEAM.md``: the owner's team card: which helpers are on, what each one
  does for this project, the exact words to say to use it, and the daily routine.

Standard library only. No network calls, no model calls.
"""

import json
import os
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
QUESTIONS_FILE = os.path.join(HERE, "questions.json")
OUTPUTS = ("profile/owner.md", "profile/answers.json", "TEAM.md")
MAX_TEXT = 400
MAX_ITEMS = 5
SECOND_BRAIN_URL = "https://github.com/KevinOrosz/SOTASECONDBRAIN"
#: The tool answer -> the installer's --harness name.
HARNESS_FOR_TOOL = {"claude": "claude", "codex": "codex", "cursor": "cursor", "copilot": "copilot",
                    "gemini": "gemini", "hermes": "hermes", "chat": "flat", "other": "generic"}
_TERMINAL = "Open the **Terminal** app (on Windows: **PowerShell**)."
OPEN_STEPS = {
    "claude": [_TERMINAL, "Type `cd {cd}` and press Enter.", "Type `claude` and press Enter. Your Lead says hello."],
    "codex": [_TERMINAL, "Type `cd {cd}` and press Enter.", "Type `codex` and press Enter."],
    "gemini": [_TERMINAL, "Type `cd {cd}` and press Enter.", "Type `gemini` and press Enter."],
    "hermes": [_TERMINAL, "Type `cd {cd}` and press Enter.",
               "The first time only: type `hermes skills trust` and press Enter, so Hermes loads your team's skills.",
               "Type `hermes` and press Enter."],
    "cursor": ["Open **Cursor**.", "Choose **File → Open Folder** and pick `{folder}`.", "Open the chat panel and type there."],
    "copilot": ["Open **VS Code**.", "Choose **File → Open Folder** and pick `{folder}`.",
                "Open **Copilot Chat** and pick the `orchestrator` agent from the agent menu."],
    "chat": ["Open your chat app and start a new chat (or a new project, if it has projects).",
             "Open `{folder}` on your computer and find `SOTACOMMAND.prompt.md`. Paste everything in it into the chat's instructions box.",
             "Paste this whole file (`TEAM.md`) and `profile/owner.md` after it. Then just talk."],
    "other": ["Open your AI app.", "Open the folder `{folder}` in it (most apps have **File → Open Folder**).",
              "Type in its chat. It reads `AGENTS.md` in that folder to learn your team."],
}


def display_folder(target: str) -> str:
    """The project folder as the owner would type it: ``~/name`` when it is in their home folder."""
    home = os.path.expanduser("~")
    full = os.path.abspath(target)
    if full == home or full.startswith(home + os.sep):
        return "~" + full[len(home):].replace(os.sep, "/")
    return full


def cd_argument(folder: str) -> str:
    """``folder`` as typed after ``cd``. A leading ``~`` stays outside the quotes, or the shell won't expand it."""
    plain = all(ch.isalnum() or ch in "/._-~" for ch in folder)
    if plain:
        return folder
    if folder.startswith("~/"):
        return '~/"%s"' % folder[2:]
    return '"%s"' % folder


def is_git_project(target: str) -> bool:
    return os.path.exists(os.path.join(target, ".git"))


class InterviewError(ValueError):
    pass


def load_script() -> Dict[str, Any]:
    with open(QUESTIONS_FILE, encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=OrderedDict)


def _choices(script: Dict[str, Any], question: Dict[str, Any]) -> List[Tuple[str, str]]:
    if question.get("choices_from") == "helpers":
        return [(h["id"], "%s: %s" % (h["name"][0].upper() + h["name"][1:], h["does"])) for h in script["helpers"]]
    return [(c["id"], c["label"]) for c in question.get("choices", [])]


def render_questions(script: Dict[str, Any]) -> str:
    """The interview as plain text, for an agent to ask one question at a time."""
    lines = ["The WIZARD interview. Ask one question at a time, in these words.", ""]
    for number, question in enumerate(script["questions"], 1):
        lines.append("%d. [%s] %s%s" % (number, question["id"], question["ask"], "" if question.get("required") else " (optional)"))
        for index, (_, label) in enumerate(_choices(script, question), 1):
            lines.append("     %d) %s" % (index, label))
        if question.get("example"):
            lines.append("     Example: %s" % question["example"])
        if question.get("default"):
            lines.append("     If they skip it: %s" % question["default"])
        if question["id"] == "helpers":
            lines.append("     If they skip it: recommend the set for their answer to [kind].")
    lines += [
        "",
        "Then, for each helper they chose, ask [focus.<helper id>] (optional):",
        "   " + script["focus_question"].format(name="<the helper's name>", does="<what it does>"),
        "",
        "Save the answers as JSON, keyed by the ids in brackets: text answers as strings,",
        "list answers as arrays of strings, choices by id, helpers as an array of helper ids,",
        'and the focus answers as {"focus": {"<helper id>": "<answer>"}}.',
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- answers


def _clean(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise InterviewError("%s must be text" % where)
    text = " ".join(value.split())
    if len(text) > MAX_TEXT:
        raise InterviewError("%s is longer than %d characters; shorten it" % (where, MAX_TEXT))
    return text


def _clean_list(value: Any, where: str) -> List[str]:
    if isinstance(value, str):
        value = [part for part in value.split(",")]
    if not isinstance(value, list):
        raise InterviewError("%s must be a list" % where)
    items = [_clean(item, where) for item in value]
    items = [item for item in items if item]
    if len(items) > MAX_ITEMS:
        raise InterviewError("%s has more than %d items; keep the most important ones" % (where, MAX_ITEMS))
    return items


def normalize(script: Dict[str, Any], raw: Dict[str, Any]) -> "OrderedDict[str, Any]":
    """Validate raw answers and fill the defaults. Raises InterviewError."""
    if not isinstance(raw, dict):
        raise InterviewError("the answers must be a JSON object")
    known = [q["id"] for q in script["questions"]] + ["focus"]
    unknown = sorted(key for key in raw if key not in known and not key.startswith("$"))
    if unknown:
        raise InterviewError("unknown answer(s): %s (known: %s)" % (", ".join(unknown), ", ".join(known)))
    helper_ids = [h["id"] for h in script["helpers"]]
    answers = OrderedDict()  # type: OrderedDict[str, Any]
    for question in script["questions"]:
        key, kind = question["id"], question["type"]
        value = raw.get(key)
        empty = value is None or value == "" or value == []
        if empty and question.get("required") and "default" not in question:
            raise InterviewError("%s is required: %s" % (key, question["ask"]))
        if kind == "text":
            answers[key] = _clean(value, key) if not empty else question.get("default", "")
            if question.get("required") and not answers[key]:
                raise InterviewError("%s is required: %s" % (key, question["ask"]))
        elif kind == "list":
            answers[key] = _clean_list(value, key) if not empty else []
        elif kind == "choice":
            ids = [c for c, _ in _choices(script, question)]
            answers[key] = question.get("default") if empty else value
            if answers[key] not in ids:
                raise InterviewError("%s must be one of: %s" % (key, ", ".join(ids)))
        elif kind == "multi":
            if empty:
                answers[key] = list(script["recommended_helpers"][answers.get("kind") or "other"])
            else:
                chosen = _clean_list(value, key) if not isinstance(value, list) else value
                bad = [item for item in chosen if item not in helper_ids]
                if bad:
                    raise InterviewError("%s: unknown helper(s) %s (known: %s)" % (key, ", ".join(map(str, bad)), ", ".join(helper_ids)))
                answers[key] = [h for h in helper_ids if h in chosen]
    focus = raw.get("focus") or {}
    if not isinstance(focus, dict):
        raise InterviewError("focus must be an object of helper id -> first job")
    answers["focus"] = OrderedDict()
    for helper in helper_ids:
        if helper in focus and helper in answers["helpers"]:
            text = _clean(focus[helper], "focus.%s" % helper)
            if text:
                answers["focus"][helper] = text
    extra = sorted(key for key in focus if key not in helper_ids)
    if extra:
        raise InterviewError("focus: unknown helper(s) %s" % ", ".join(extra))
    return answers


# --------------------------------------------------------------------------- terminal


def ask_in_terminal(script: Dict[str, Any], read: Callable[[str], str], write: Callable[[str], None]) -> Dict[str, Any]:
    """Ask every question on the terminal. ``read`` is input(); ``write`` prints."""
    raw = {}  # type: Dict[str, Any]
    total = len(script["questions"])
    write("WIZARD interview: %d short questions. Press Enter to skip an optional one.\n" % total)
    for number, question in enumerate(script["questions"], 1):
        choices = _choices(script, question)
        while True:
            write("\n%d of %d. %s" % (number, total, question["ask"]))
            for index, (_, label) in enumerate(choices, 1):
                write("   %d) %s" % (index, label))
            if question.get("example"):
                write("   Example: %s" % question["example"])
            if question["type"] == "multi":
                recommended = script["recommended_helpers"][raw.get("kind") or "other"]
                picks = ",".join(str(i) for i, (cid, _) in enumerate(choices, 1) if cid in recommended)
                write("   Type the numbers, like 1,2,4. Press Enter for the ones we suggest (%s)." % picks)
            elif choices:
                write("   Type a number.%s" % (" Press Enter for: %s." % question["default"] if question.get("default") else ""))
            elif question.get("default"):
                write("   Press Enter for: %s" % question["default"])
            reply = read("> ").strip()
            try:
                if choices and reply:
                    numbers = [int(part) for part in reply.replace(" ", "").split(",") if part]
                    if any(n < 1 or n > len(choices) for n in numbers):
                        raise ValueError
                    picked = [choices[n - 1][0] for n in numbers]
                    value = picked if question["type"] == "multi" else picked[0]  # type: Any
                    if question["type"] == "choice" and len(picked) != 1:
                        raise ValueError
                else:
                    value = reply
                if question.get("required") and not value and "default" not in question:
                    write("   This one is needed. A few words are enough.")
                    continue
                if question["type"] == "text" and value:
                    _clean(value, "your answer")
                if question["type"] == "list" and value:
                    _clean_list(value, "your answer")
                raw[question["id"]] = value
                break
            except ValueError as exc:
                raw.pop(question["id"], None)
                write("   %s" % (exc if isinstance(exc, InterviewError) else "Please type a number from the list."))
    answers = normalize(script, raw)
    focus = {}
    by_id = {h["id"]: h for h in script["helpers"]}
    for helper in answers["helpers"]:
        info = by_id[helper]
        write("\n" + script["focus_question"].format(name=info["name"], does=info["does"]))
        write("   Press Enter to skip.")
        reply = read("> ").strip()
        if reply:
            focus[helper] = reply
    raw["focus"] = focus
    return raw


# --------------------------------------------------------------------------- outputs


def _bullets(items: List[str], empty: str) -> str:
    return "\n".join("- %s" % item for item in items) if items else "- %s" % empty


def render_profile(answers: Dict[str, Any]) -> str:
    return "\n".join([
        "# About the owner",
        "",
        "Written by the WIZARD interview. Every agent reads this at the start of a session.",
        "Agents can't edit it: to change it, run the interview again with `--redo`.",
        "",
        "- **Owner:** %s" % answers["name"],
        "- **Project:** %s" % answers["project"],
        "- **What it is:** %s" % answers["what"],
        "- **Who it is for:** %s" % answers["audience"],
        "- **Voice:** %s" % answers["voice"],
        "",
        "## What the owner wants help with",
        "",
        _bullets(answers["goals"], "Not set yet. Ask the owner."),
        "",
        "## Always ask the owner first",
        "",
        "- %s" % answers["approval"],
        "",
        "## Never",
        "",
        _bullets(answers["never"], "Nothing listed."),
        "",
    ])


def render_team(script: Dict[str, Any], answers: Dict[str, Any], folder: str = "your project folder") -> str:
    fill = {"project": answers["project"], "audience": answers["audience"]}
    on = [h for h in script["helpers"] if h["id"] in answers["helpers"]]
    off = [h for h in script["helpers"] if h["id"] not in answers["helpers"]]
    lines = [
        "# %s: your AI team" % answers["project"],
        "",
        "Hi %s. This is your team card. It says who is on your team, what each helper does, and the exact words to use." % answers["name"],
        "",
        "## How to open your team",
        "",
    ]
    lines += ["%d. %s" % (n, step.format(folder=folder, cd=cd_argument(folder))) for n, step in enumerate(OPEN_STEPS[answers["tool"]], 1)]
    lines += [
        "",
        "Do this every time you want to work with your team.",
        "",
        "## How to use your team",
        "",
        "1. Say what you want in plain words. **The Lead** (the role called `orchestrator`) hears you and hands the job to the right helper.",
        "2. To pick a helper yourself, start with its name, like `Researcher: ...`.",
        "3. When you stop for the day, say **`close`**. The team writes a short note so it remembers tomorrow.",
        "",
        "Lost? Type **`WIZARD`**. It checks everything and shows you this card again.",
        "",
        "## Your helpers",
        "",
        "| Helper | Role name | What it does for you | Try saying |",
        "|---|---|---|---|",
        "| The Lead | `orchestrator` | Listens to you, picks the right helper, checks the work before you see it | *What should we work on first?* |",
    ]
    for helper in on:
        first = answers["focus"].get(helper["id"])
        say = "%s: %s" % (helper["name"][4:], first) if first else helper["first_job"].format(**fill)
        lines.append("| %s | `%s` | %s | *%s* |" % (
            helper["name"][0].upper() + helper["name"][1:], helper["id"], helper["does"][0].upper() + helper["does"][1:], say.replace("|", "/")))
    lines += ["", "## Helpers that are off", ""]
    if off:
        lines.append("The Lead won't hand work to these. In Claude Code the tool refuses it; in other tools it is a rule the Lead follows. To turn one on, run the interview again with `--redo`.")
        lines.append("")
        lines += ["- %s (`%s`): %s" % (h["name"][0].upper() + h["name"][1:], h["id"], h["does"]) for h in off]
    else:
        lines.append("None. Every helper is on.")
    lines += [
        "",
        "## Your goals",
        "",
        _bullets(answers["goals"], "Not set yet. Tell the Lead what you want to get done."),
        "",
        "## Your rules",
        "",
        "- **Always ask me first:** %s" % answers["approval"],
        "- **Sound like:** %s" % answers["voice"],
    ]
    lines += ["- **Never:** %s" % item for item in answers["never"]]
    lines += [
        "",
        "The full version is in `profile/owner.md`. Your helpers read it every session and can't change it.",
        "",
        "## Every day",
        "",
        "- **Start:** open your team (the steps at the top). The team reads `memory/HANDOFF.md` (yesterday's note) and tells you where things stand.",
        "- **Work:** ask for things. Approve or correct what comes back.",
        "- **Stop:** say `close`. The team rewrites the note.",
        "- **Any time:** ask *what did we decide about ...?* The team keeps every day's note and searches them.",
        "- **Once a week:** say *Run the owner-auditor for this week* to see where your time went.",
        "- **When the Time-Keeper proposes a skill:** it noticed the same problem coming back. Say *approve skill <name>* to try it. It only counts as proven once the problem stops.",
        "",
        "## Long-term memory",
        "",
    ]
    if answers["second_brain"] == "yes":
        lines.append("You asked for a second brain. Get SOTASECONDBRAIN (%s), open it in your AI tool and type `WIZARD` there too. Then connect the two: the steps are in `integrations/second-brain/README.md` in your SOTACOMMAND folder." % SECOND_BRAIN_URL)
    else:
        lines.append("Your team remembers day to day with short notes. When you want it to remember for months, add SOTASECONDBRAIN (%s): a private notebook your helpers propose pages for, and only you approve." % SECOND_BRAIN_URL)
    return "\n".join(lines) + "\n"


def write_outputs(script: Dict[str, Any], answers: Dict[str, Any], target: str, redo: bool = False, dry_run: bool = False) -> Tuple[List[str], List[str]]:
    """Write the three files. Returns (report lines, written relative paths)."""
    existing = [rel for rel in OUTPUTS if os.path.exists(os.path.join(target, *rel.split("/")))]
    if existing and not redo:
        raise InterviewError(
            "%s already exist(s). Your answers are safe. To replace them with new answers, add --redo" % ", ".join(existing))
    contents = {
        "profile/owner.md": render_profile(answers),
        "profile/answers.json": json.dumps(answers, indent=2, ensure_ascii=False) + "\n",
        "TEAM.md": render_team(script, answers, display_folder(target)),
    }
    report, written = [], []
    for rel in OUTPUTS:
        path = os.path.join(target, *rel.split("/"))
        if dry_run:
            report.append("would write %s" % rel)
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(contents[rel])
        report.append("%s %s" % ("replaced" if rel in existing else "wrote", rel))
        written.append(rel)
    return report, written
