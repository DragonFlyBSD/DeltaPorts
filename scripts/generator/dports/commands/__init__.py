"""
DPorts v2 command implementations.

Each command module exports a single function that implements the command logic.
Commands are registered with the CLI via the COMMANDS dict.
"""

from dports.commands.merge import cmd_merge
from dports.commands.sync import cmd_sync
from dports.commands.prune import cmd_prune
from dports.commands.makefiles import cmd_makefiles
from dports.commands.check import cmd_check
from dports.commands.migrate import cmd_migrate
from dports.commands.state import cmd_state
from dports.commands.list import cmd_list
from dports.commands.status import cmd_status
from dports.commands.verify import cmd_verify
from dports.commands.add import cmd_add
from dports.commands.save import cmd_save
from dports.commands.diff import cmd_diff
from dports.commands.special import cmd_special
from dports.commands.logs import cmd_logs
from dports.commands.update import cmd_update
from dports.commands.compose import cmd_compose

COMMANDS = {
    # Core commands (v2)
    "merge": cmd_merge,
    "sync": cmd_sync,
    "prune": cmd_prune,
    "makefiles": cmd_makefiles,
    # New v2 commands
    "check": cmd_check,
    "migrate": cmd_migrate,
    "state": cmd_state,
    "list": cmd_list,
    # Ported from v1
    "status": cmd_status,
    "verify": cmd_verify,
    "add": cmd_add,
    "save": cmd_save,
    "diff": cmd_diff,
    "special": cmd_special,
    "logs": cmd_logs,
    "update": cmd_update,
    "compose": cmd_compose,
}

__all__ = ["COMMANDS"]
