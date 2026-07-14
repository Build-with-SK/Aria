"""
ARIA terminal display utilities.
Clean, professional CLI output with colour support.
"""

import os
import sys
import re


class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    PURPLE  = "\033[35m"
    CYAN    = "\033[36m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    RED     = "\033[31m"
    BLUE    = "\033[34m"
    WHITE   = "\033[97m"
    BPURPLE = "\033[1;35m"
    BCYAN   = "\033[1;36m"


def supports_colour():
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


USE_COLOUR = supports_colour()


def c(colour, text):
    if USE_COLOUR:
        return f"{colour}{text}{C.RESET}"
    return text


BANNER = r"""
  ╔══════════════════════════════════════════════════════════╗
  ║   ARIA — Adaptive Reasoning Intelligence Architecture    ║
  ║   Cognitive AI layer · Trading Intelligence System       ║
  ╚══════════════════════════════════════════════════════════╝
"""

HELP_TEXT = """
Commands:
  /chart <path>   — Analyse a chart image (jpg/png)
  /debate         — Run Bull/Bear/Risk/Quant/Macro debate
  /scenario       — Run a macro risk scenario
  /watchlist      — Show vault watchlist
  /memory         — Show current working memory state
  /save           — Save session to Obsidian vault
  /clear          — Clear conversation (keep memory)
  /help           — Show this menu
  /exit           — Save and exit

Or just talk: "What's the read on NVDA?" "Is this a good entry?"
"""


def print_banner():
    print(c(C.BPURPLE, BANNER))


def print_help():
    print(c(C.DIM, HELP_TEXT))


def print_aria(text: str):
    if USE_COLOUR:
        text = re.sub(r"\*\*(.+?)\*\*", lambda m: f"{C.BOLD}{m.group(1)}{C.RESET}", text)
    label = c(C.BPURPLE, "[ARIA]")
    print(f"\n{label} {text}\n")


def print_tool_use(name: str, inputs: dict):
    import json
    label = c(C.CYAN, f"[TOOL:{name}]")
    print(f"  {label} {json.dumps(inputs, default=str)}")


def print_error(message: str):
    label = c(C.RED, "[ERROR]")
    print(f"{label} {message}")


def print_info(message: str):
    label = c(C.BLUE, "[INFO]")
    print(f"{label} {message}")


def print_success(message: str):
    label = c(C.GREEN, "[OK]")
    print(f"{label} {message}")


def spinner(message: str):
    print(c(C.DIM, f"  ... {message}"), end="\r", flush=True)