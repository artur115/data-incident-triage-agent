"""What actually happens to a proposal. Plain code, not the model.

The agent proposes close / reload / escalate. This gate decides, from the table's
criticality tier, whether that runs on its own or waits for a person:

  close     runs on its own; the alert, the diagnosis and the evidence are logged
  reload    runs on its own for tier 2 and 3; tier 1 asks the owner to approve first
  escalate  pages the owner of the root-cause table

A model can't talk its way past this: the tier comes from meta.tables, not from
the model's answer.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Decision:
    executed: str        # what happened: closed | reloaded | approval_requested | paged
    needs_person: bool
    route_to: str        # owner who gets the message


def decide(proposed_action: str, tier: int, owner: str) -> Decision:
    if proposed_action == "close":
        return Decision("closed", False, owner)
    if proposed_action == "reload":
        if tier == 1:
            return Decision("approval_requested", True, owner)
        return Decision("reloaded", False, owner)
    return Decision("paged", True, owner)
