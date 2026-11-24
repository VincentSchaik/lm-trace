"""Find candidate action items in a meeting transcript.

Input: the transcript on stdin, one line per turn ("Name: text").
Output: JSON on stdout: {"action_items": [{"owner", "action", "due", "line"}], "count"}.
Rules: a line where the speaker says "I will / I'll / I can", or asks a named person "X, can you…", or has
"action:" or "todo". The list is a start: the agent checks each item against the transcript.
Standard library only, no input prompts.
"""
import json, re, sys

DAYS = r"(?:today|tomorrow|tonight|next week|this week|(?:on |by )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|end of (?:day|week|month)|(?:by |on )?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2})"


def due_of(text):
    m = re.search(DAYS, text, re.I)
    return m.group(0).strip() if m else "not set"


def main():
    items = []
    names = set()
    lines = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]
    for l in lines:
        m = re.match(r"^([A-Z][\w.-]*(?: [A-Z][\w.-]*)?):\s*(.+)$", l)
        if m and m.group(1).lower() not in ("action", "todo"):
            names.add(m.group(1))
    for n, l in enumerate(lines, 1):
        m = re.match(r"^([A-Z][\w.-]*(?: [A-Z][\w.-]*)?):\s*(.+)$", l)
        speaker, text = (m.group(1), m.group(2)) if m and m.group(1).lower() not in ("action", "todo") else (None, l)
        own = re.search(r"\b(?:I will|I'll|I can|I am going to|I'm going to)\s+(.+?)(?:[.!]|$)", text, re.I)
        ask = re.search(r"\b([A-Z][a-z]+),?\s+(?:can|could|will) you\s+(.+?)(?:[?.!]|$)", text)
        tag = re.search(r"\b(?:action|todo)\s*:\s*(.+?)(?:[.!]|$)", text, re.I)
        if own and speaker:
            items.append({"owner": speaker, "action": own.group(1).strip(), "due": due_of(text), "line": n})
        elif ask and ask.group(1) in names:
            items.append({"owner": ask.group(1), "action": ask.group(2).strip(), "due": due_of(text), "line": n})
        elif tag:
            items.append({"owner": "not set", "action": tag.group(1).strip(), "due": due_of(text), "line": n})
    print(json.dumps({"action_items": items, "count": len(items)}, indent=2))


if __name__ == "__main__":
    main()
