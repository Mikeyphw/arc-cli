from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandDoc:
    name: str
    summary: str
    aliases: tuple[str, ...] = ()
    section: int = 1
    synopsis: str | None = None

    @property
    def man_topic(self) -> str:
        return self.name

    @property
    def man_filename(self) -> str:
        stem = "arc" if self.name == "arc" else f"arc-{self.name}"
        return f"{stem}.{self.section}"


@dataclass(frozen=True, slots=True)
class AliasSpec:
    executable: str
    command: str
    summary: str


# Shared command metadata. argparse help, executable alias dispatch, completion,
# man-topic resolution, packaging-contract tests, and documentation checks all
# consume this table so those surfaces cannot silently invent their own names.
COMMAND_DOCS: dict[str, CommandDoc] = {
    "identify": CommandDoc("identify", "identify archive format", synopsis="arc identify FILE..."),
    "create": CommandDoc("create", "create an archive or compressed stream", ("arcmk", "arc-create", "arcpack"), synopsis="arc create ARCHIVE INPUT... [OPTIONS]"),
    "extract": CommandDoc("extract", "extract an archive safely", ("arcx", "arc-extract", "arcunpack"), synopsis="arc extract ARCHIVE [MEMBER...] [OPTIONS]"),
    "list": CommandDoc("list", "list archive members", ("arcls", "arc-list"), synopsis="arc list ARCHIVE [MEMBER...] [OPTIONS]"),
    "info": CommandDoc("info", "show archive metadata and summary information", ("arci", "arc-info"), synopsis="arc info ARCHIVE... [OPTIONS]"),
    "test": CommandDoc("test", "verify archive integrity", ("arct", "arc-test", "arccheck"), synopsis="arc test ARCHIVE [OPTIONS]"),
    "convert": CommandDoc("convert", "convert an archive or compressed stream", ("arccv", "arc-convert", "arcconvert"), synopsis="arc convert SOURCE [DESTINATION] [FORMAT-SELECTOR] [OPTIONS]"),
    "add": CommandDoc("add", "add new archive members", ("arca", "arc-add"), synopsis="arc add ARCHIVE INPUT... [OPTIONS]"),
    "update": CommandDoc("update", "update archive members", ("arcu", "arc-update"), synopsis="arc update ARCHIVE INPUT... [OPTIONS]"),
    "remove": CommandDoc("remove", "remove archive members", ("arcrm", "arc-remove"), synopsis="arc remove ARCHIVE MEMBER... [OPTIONS]"),
    "backends": CommandDoc("backends", "inspect native backend capabilities", ("arcbe", "arc-backends"), synopsis="arc backends [OPTIONS]"),
    "formats": CommandDoc("formats", "show supported formats", ("arc-formats",), synopsis="arc formats [OPTIONS]"),
    "profiles": CommandDoc("profiles", "show configured profiles", ("arcp", "arc-profiles"), synopsis="arc profiles [OPTIONS]"),
    "aliases": CommandDoc("aliases", "inspect installed executable aliases", synopsis="arc aliases [--json] [--missing]"),
    "doctor": CommandDoc("doctor", "audit Arc installation and runtime health", synopsis="arc doctor [--json] [--fix] [--source PATH]"),
    "explain": CommandDoc("explain", "explain an execution plan without mutating data", synopsis="arc explain [--json] COMMAND ..."),
    "recover": CommandDoc("recover", "inspect and clean interrupted Arc transactions", synopsis="arc recover [TRANSACTION_ID] [--cleanup] [--json]"),
    "schema": CommandDoc("schema", "show Arc machine-contract JSON Schemas", synopsis="arc schema [machine-v1|backend-capability-v1|verification-evidence-v1] [--list]"),
    "completion": CommandDoc("completion", "manage shell completion", synopsis="arc completion ACTION [LOCATION]"),
    "man": CommandDoc("man", "open Arc manual pages", synopsis="arc man [TOPIC]"),
    "help": CommandDoc("help", "open detailed help for an Arc command", synopsis="arc help [TOPIC]"),
}

REFERENCE_DOCS: dict[str, CommandDoc] = {
    "arc": CommandDoc("arc", "Arc command overview", section=1, synopsis="arc [GLOBAL OPTIONS] COMMAND ..."),
    "config": CommandDoc("config", "Arc configuration file", section=5),
    "remote": CommandDoc("remote", "SSH and rclone transport semantics", section=7),
    # Formats/backends/profiles have command pages and reference pages. Topic
    # resolution defaults to the reference page for the noun itself.
}

ALIAS_SPECS: tuple[AliasSpec, ...] = tuple(
    AliasSpec(alias, name, doc.summary)
    for name, doc in COMMAND_DOCS.items()
    for alias in doc.aliases
)

ALIAS_TO_COMMAND: dict[str, str] = {spec.executable: spec.command for spec in ALIAS_SPECS}

# Names installed as console scripts. This compatibility mapping is derived
# from the canonical alias registry rather than maintained independently.
EXECUTABLE_ALIASES: dict[str, str] = dict(ALIAS_TO_COMMAND)


def console_script_mapping() -> dict[str, str]:
    """Return the canonical installed console-script contract."""
    target = "arc_cli.cli:main"
    return {"arc": target, **{spec.executable: target for spec in ALIAS_SPECS}}


def console_script_names() -> tuple[str, ...]:
    return tuple(console_script_mapping())


def alias_specs() -> tuple[AliasSpec, ...]:
    return ALIAS_SPECS


def resolve_topic(value: str) -> str:
    topic = value.strip()
    if topic in {"arc", "overview"}:
        return "arc"
    if topic in ALIAS_TO_COMMAND:
        topic = ALIAS_TO_COMMAND[topic]
    if topic.startswith("arc-") and topic[4:] in COMMAND_DOCS:
        topic = topic[4:]
    return topic


def aliases_for(command: str) -> tuple[str, ...]:
    doc = COMMAND_DOCS.get(command)
    return doc.aliases if doc else ()


def canonical_command(value: str) -> str | None:
    topic = resolve_topic(value)
    return topic if topic in COMMAND_DOCS else None
