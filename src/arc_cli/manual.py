from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .command_docs import COMMAND_DOCS, aliases_for, canonical_command
from .formats import CREATE_SUFFIX_SHORTCUTS


@dataclass(frozen=True, slots=True)
class ManualPage:
    name: str
    section: int
    title: str
    sections: tuple[tuple[str, str], ...]

    @property
    def filename(self) -> str:
        return f"{self.name}.{self.section}"


_FORMAT_LINES = "\n".join(
    f"  {flag:<10} {fmt:<10} default suffix {suffix}"
    for flag, fmt, suffix in CREATE_SUFFIX_SHORTCUTS
)
_ALIAS_LINES = "\n".join(
    f"  {alias:<14} arc {command}"
    for command, doc in COMMAND_DOCS.items()
    for alias in doc.aliases
)


def _command_page(
    name: str,
    description: str,
    *,
    options: str = "",
    semantics: str = "",
    examples: str = "",
    extra_sections: tuple[tuple[str, str], ...] = (),
    see_also: str = "arc(1), arc-formats(7), arc-backends(7), arc-remote(7)",
) -> ManualPage:
    doc = COMMAND_DOCS[name]
    synopsis = doc.synopsis or f"arc {name} [OPTIONS]"
    alias_lines = [f"{alias} ..." for alias in aliases_for(name)]
    if alias_lines:
        synopsis += "\n" + "\n".join(alias_lines)
    sections: list[tuple[str, str]] = [
        ("NAME", f"arc-{name} - {doc.summary}"),
        ("SYNOPSIS", synopsis),
        ("DESCRIPTION", description),
    ]
    if options:
        sections.append(("OPTIONS", options))
    if semantics:
        sections.append(("SEMANTICS", semantics))
    sections.extend(extra_sections)
    if examples:
        sections.append(("EXAMPLES", examples))
    sections.append(("SEE ALSO", see_also))
    return ManualPage(f"arc-{name}", 1, f"ARC-{name.upper()}", tuple(sections))


PAGES: dict[tuple[str, int], ManualPage] = {}


def _add(page: ManualPage) -> None:
    PAGES[(page.name, page.section)] = page


_add(
    ManualPage(
        "arc",
        1,
        "ARC",
        (
            ("NAME", "arc - safe, backend-aware archive and compression utility"),
            ("SYNOPSIS", "arc COMMAND [COMMAND OPTIONS] ...\narc-COMMAND ...\nSHORT-ALIAS ..."),
            (
                "DESCRIPTION",
                "Arc provides one normalized interface over native archive and compression backends. "
                "It owns format detection, safe extraction, filtering, atomic publication, backend capability "
                "selection, progress, remote transport, conversion, logical provenance/diff, inspection, completion, and machine-readable output.",
            ),
            (
                "COMMANDS",
                "\n".join(
                    f"  {name:<12} {doc.summary}"
                    for name, doc in COMMAND_DOCS.items()
                    if name not in {"man", "help"}
                )
                + "\n  help         open detailed bundled help\n  man          open Arc manual pages",
            ),
            ("EXECUTABLE ALIASES", _ALIAS_LINES),
            (
                "FORMAT SELECTORS",
                _FORMAT_LINES
                + "\n\nCreate and convert short selectors are authoritative. When a caller supplies an "
                "extensionless destination, a short selector appends its represented suffix. Plain -F/--format "
                "only appends to an explicitly supplied extensionless path when --add-extension is requested. "
                "An omitted convert destination is always derived with the selected target suffix.",
            ),
            (
                "MACHINE-READABLE IDENTITY",
                "Bare --json preserves Arc's pre-R08 command-specific JSON shape for compatibility. Explicit --json=v1 "
                "emits the stable arc.machine/v1 envelope with schema_version, typed result/error/diagnostic fields, a "
                "redacted received argv, and canonical command identity. Installed aliases such as arci/arccv preserve "
                "their literal executable identity while resolving to the canonical command.",
            ),
            (
                "SAFETY",
                "Arc rejects traversal, absolute member paths, unsafe link parents, and special objects before "
                "normalized extraction. Conversion builds an unpublished destination candidate, verifies it, then "
                "publishes it atomically. --replace-source cannot remove the source until verified publication succeeds.",
            ),
            (
                "REMOTE PATHS",
                "SSH locations use ssh://NAME/path or configured NAME:path syntax. rclone locations use "
                "rclone://NAME/path or normal remote:path syntax. Operations that need random access or local safety "
                "inspection may stage data; diagnostic strategy labels say so instead of claiming a direct stream.",
            ),
            (
                "ENVIRONMENT",
                "ARC_PROGRESS, ARC_LEVEL, ARC_THREADS, ARC_BACKEND_*, ARC_CACHE_HOME, ARC_STATE_HOME, XDG_CONFIG_HOME, "
                "XDG_CACHE_HOME, XDG_STATE_HOME, RCLONE_CONFIG, PAGER, MANPAGER, NO_COLOR.",
            ),
            ("FILES", "~/.config/arc/config.toml\n~/.cache/arc/\n~/.local/state/arc/transactions/\n~/.local/state/arc/batches/\nInstalled manual pages under share/man."),
            (
                "EXIT STATUS",
                "0 means success. Usage, unsupported format/backend, conflict, password, corruption, and "
                "unsafe-archive failures use Arc's typed non-zero exit statuses.",
            ),
            (
                "SEE ALSO",
                "arc-create(1), arc-extract(1), arc-list(1), arc-info(1), arc-diff(1), arc-test(1), arc-convert(1), "
                "arc-formats(7), arc-backends(7), arc-remote(7), arc-config(5), arc-profiles(5)",
            ),
        ),
    )
)

_add(
    _command_page(
        "identify",
        "Detect archive/compression format using content first and the filename suffix only as a hint or fallback.",
        options="-F, --format FORMAT\n--json\n--execution MODE",
        examples="arc identify mystery.bin\narc identify a.zip b.tar.zst --json",
    )
)
_add(
    _command_page(
        "create",
        "Create a new archive or compressed stream transactionally. Format may be inferred from the destination "
        "or selected explicitly with -F/--format or a short suffix selector.",
        options=(
            "-F, --format FORMAT\n"
            + _FORMAT_LINES
            + "\n--add-extension\n--overwrite\n--level 0..9\n--threads N\n"
            "--include/--exclude and rule files\n--follow-symlinks\n--one-file-system"
        ),
        semantics=(
            "A short suffix selector is authoritative even when the destination has a conflicting recognized suffix. "
            "If the destination has no recognized suffix, the selector appends its represented suffix. Plain -F/--format "
            "keeps an explicitly supplied extensionless destination unless --add-extension is present."
        ),
        examples="arc create backup src/ -tzst\narcmk photos Photos/ -7z --level 8\narc create misleading.rar -7z data/",
    )
)
_add(
    _command_page(
        "extract",
        "Safely extract selected archive members or a compressed stream. Arc indexes archive members first and "
        "enforces normalized traversal, link-parent, conflict, and filtering rules.",
        options=(
            "-o, --output DIR\n--overwrite | --skip-existing | --rename-existing\n--stdout\n--unsafe-paths\n"
            "--include/--exclude and rule files\n--password/--password-file/--password-env"
        ),
        examples="arcx backup.zip -o restored/\narc extract backup.7z docs/readme.txt --stdout",
    )
)
_add(
    _command_page(
        "list",
        "List normalized archive member metadata. Stream-only compressors have no member list.",
        options="--json\n--include/--exclude and rule files\n--password/--password-file/--password-env\n--backend NAME",
        examples="arcls backup.tar.zst\narc list private.7z --password-env ARCHIVE_PASS --json",
        see_also="arc-info(1), arc-test(1), arc-extract(1)",
    )
)
_add(
    _command_page(
        "info",
        "Show a compact archive-level summary without silently performing a full integrity test. Local and remote "
        "inputs may be mixed. Unknown values remain null/unknown rather than being reported as false.",
        options=(
            "--members        include compact member statistics\n"
            "--fingerprint    compute logical content + byte-level archive fingerprints\n"
            "--verify         run the strongest verification the selected backend can prove\n"
            "--verify-level LEVEL   none|structure|members|full\n"
            "--allow-verification-downgrade\n"
            "--technical      include backend-oriented technical metadata\n"
            "-F, --format FORMAT\n--backend NAME\n--no-fallback\n"
            "--password/--password-file/--password-env\n--show-command\n--show-native[=before|after|both]\n--json[=legacy|v1]"
        ),
        semantics=(
            "Default info is metadata inspection, not verification: JSON uses verified=null unless --verify or --verify-level was requested. "
            "Verification evidence records requested and achieved levels, backend, checks, and any explicitly authorized downgrade. "
            "Encrypted-header archives still return outer metadata when member indexing needs a password. Content detection "
            "wins over a misleading extension and the mismatch is reported explicitly. Gzip inspection reports the trailer "
            "size hint and optional embedded original filename when present. Member-backed summaries normalize oldest/newest "
            "timestamps and expose counts, largest-member evidence with --members, and encryption/solid/volume/comment-like "
            "metadata when the selected backend can prove it. --fingerprint materializes readable member content through Arc's normalized "
            "safe extraction path and reports a format-independent logical digest, a metadata digest, and the byte SHA-256 of the encoded archive. "
            "Remote random-access inspection reports when it staged locally."
        ),
        examples=(
            "arci backup.tar.zst\narci private.7z --password-env ARCHIVE_PASS\n"
            "arci archive.zip --technical\narci archive.zip --verify\narci archive.zip --fingerprint\narc info a.zip b.7z --json"
        ),
        see_also="arc-list(1), arc-diff(1), arc-test(1), arc-convert(1), arc-remote(7)",
    )
)
_add(
    _command_page(
        "diff",
        "Compare two archives by normalized logical content rather than compressed bytes. Arc fingerprints readable member bytes, "
        "normalizes member paths, preserves empty directories, and reports encoding/container and metadata differences separately.",
        options=(
            "--backend NAME\n--no-fallback\n--password/--password-file/--password-env\n"
            "--left-password/--left-password-file/--left-password-env\n"
            "--right-password/--right-password-file/--right-password-env\n"
            "--show-command\n--show-native[=before|after|both]\n--json[=legacy|v1]"
        ),
        semantics=(
            "logical=true means normalized member paths, kinds, file sizes/content SHA-256 values, link targets, and empty-directory presence match. "
            "Timestamps are intentionally excluded from the logical digest and compared through a separate metadata digest so repackaging does not "
            "turn an encoding-only change into a content change. Non-empty directory entries are normalized away because some archive formats emit them "
            "explicitly while others imply them from child paths. Single-stream compressors use the synthetic @stream logical member, allowing the same "
            "decompressed bytes in gzip/xz/zstd/bzip2 to compare independently of filenames or compression encoding. Arc still reports each archive's byte "
            "SHA-256 and format, so logical equivalence never implies byte identity. The encoding_only flag is true only when both logical content and selected metadata match while encoded bytes differ. "
            "Remote inputs use the existing read-side transport and are staged locally for normalized comparison; native remote comparison/capability negotiation remains owned by R09B."
        ),
        examples=(
            "arc diff old.zip new.7z\n"
            "arcdiff backup.zip backup.tar.zst --json\n"
            "arc diff private-a.7z private-b.zip --left-password-env OLD_PASS --right-password-env NEW_PASS --json=v1"
        ),
        see_also="arc-info(1), arc-test(1), arc-formats(7)",
    )
)

_add(
    _command_page(
        "test",
        "Run integrity verification at an explicit proof level. Unlike arc info, this command is explicitly a verification operation and reports what was actually proven.",
        options="--json[=legacy|v1]\n--verify-level none|structure|members|full\n--allow-verification-downgrade\n--include/--exclude and rule files\n--password/--password-file/--password-env\n--backend NAME",
        semantics=(
            "Proof levels are evidence contracts, not quality labels. none performs no integrity proof. structure proves a readable container/member index; "
            "single compressed streams have no index, so Arc uses the stronger native full stream test. members adds Arc's normalized member path/type safety validation "
            "but is not a per-member content checksum. full runs the selected backend integrity test that consumes encoded archive/compressed data. "
            "An explicit level cannot silently degrade: a weaker proof requires --allow-verification-downgrade and records requested/achieved levels plus the reason."
        ),
        examples="arct backup.zip\narc test private.7z --password-env ARCHIVE_PASS",
        see_also="arc-info(1), arc-list(1)",
    )
)
_add(
    _command_page(
        "convert",
        "Transform one logical archive representation into another through Arc's normalized content detection, member "
        "selection, safety validation, destination creation, integrity verification, and publication rules. SOURCE is "
        "preserved unless --replace-source is explicit.",
        options=(
            "-F, --format FORMAT\n"
            + _FORMAT_LINES
            + "\n--add-extension\n-f, --force\n--batch\n--resume\n--batch-id ID\n--replace-source\n--prove-equivalent\n"
            "--source-password/--source-password-file/--source-password-env\n"
            "--password/--password-file/--password-env\n--include/--exclude and rule files\n"
            "--level 0..9\n--threads N\n--backend NAME\n--no-fallback\n--dry-run\n"
            "--show-command\n--show-native[=before|after|both]\n--verify-level none|structure|members|full\n"
            "--allow-verification-downgrade\n--json[=legacy|v1]"
        ),
        semantics=(
            "A short selector or -F/--format controls the actual target encoding even when DESTINATION has a conflicting "
            "recognized suffix. With no destination, Arc derives a new sibling name by replacing one recognized source suffix. "
            "For an explicitly supplied extensionless destination, a short selector appends its suffix; plain -F/--format "
            "requires --add-extension to append one. Compatible single-stream conversions and unfiltered TAR-compression changes "
            "use byte-stream pipelines locally. Container-to-container conversions use an isolated safe member tree. When remote "
            "transport forces local staging, the displayed strategy is prefixed transport-staged rather than claiming a direct remote stream."
        ),
        extra_sections=(
            (
                "STREAM AND MEMBER SELECTION",
                "Container-to-gzip/bzip2/xz/zstd requires exactly one selected regular file. Include-only filters act as an "
                "explicit selection, so --include file can narrow a multi-member container. Stream-to-container conversion "
                "uses a logical filename derived from the stream source rather than Arc's temporary staging path.",
            ),
            (
                "BATCH CONVERSION",
                "Multiple sources with an explicit target format are independent batch jobs. Arc resolves and collision-checks "
                "the complete destination set before starting the first conversion, so a late filename conflict cannot leave an "
                "earlier batch item already published. --batch resolves the ambiguous two-source case. Batch runs persist a "
                "manifest under Arc's state directory. --resume reuses a completed item only when its source fingerprint and "
                "verified destination fingerprint still match; changed inputs invalidate only the affected item. Remote items are "
                "not reused unless Arc can prove a stable remote identity. --prove-equivalent fingerprints the readable source and the unpublished destination with the same "
                "logical provenance engine as arc diff; a logical mismatch blocks local publication. Remote destinations are re-read and fingerprinted after publication. "
                "Because it is an assertion of semantic preservation, intentional content-changing filters are expected to fail equivalence proof.",
            ),
            (
                "PUBLICATION AND VERIFICATION",
                "Local output is created at an unpublished same-filesystem candidate and processed according to the requested "
                "verification policy before publication. none deliberately publishes without proof and cannot authorize "
                "--replace-source. structure/members/full are negotiated against typed backend capabilities; a weaker proof is "
                "accepted only with --allow-verification-downgrade and is recorded explicitly. A failed requested proof leaves no "
                "new corrupt final path and does not clobber an existing --force destination. Verified remote output is re-read "
                "after publication before --replace-source may delete any source.",
            ),
        ),
        examples=(
            "arc convert old.zip -tzst\n"
            "arc convert private.rar archive.7z -7z --source-password-env OLD_PASS --password-env NEW_PASS\n"
            "arc convert archive.zip -zst --include video.mp4\n"
            "arc convert database.sql.gz -zst\n"
            "arc convert backup.zip backup.tar.zst --prove-equivalent\n"
            "arc convert a.zip b.zip c.zip -tzst --replace-source"
        ),
        see_also="arc-create(1), arc-info(1), arc-formats(7), arc-remote(7)",
    )
)
_add(
    _command_page(
        "add",
        "Add new archive members under Arc's normalized input/filter/backend policy. Existing member names are conflicts; use update when replacement is intended.",
        examples="arca files.zip new.txt",
        see_also="arc-create(1), arc-update(1), arc-remove(1)",
    )
)
_add(
    _command_page(
        "update",
        "Update existing members or add changed inputs where the selected backend and format support normalized update semantics.",
        examples="arcu files.7z changed/",
        see_also="arc-create(1), arc-add(1), arc-remove(1)",
    )
)
_add(
    _command_page(
        "remove",
        "Remove explicitly selected members when the selected format/backend can provide normalized mutation semantics. Unsupported compressed-container mutations fail rather than silently recreating with different semantics.",
        options="--backend NAME\n--password/--password-file/--password-env\n--dry-run",
        examples="arcrm plain.tar old/path.txt",
        see_also="arc-add(1), arc-update(1)",
    )
)
_add(
    _command_page(
        "backends",
        "Report configured backend preference order, installed binaries, and the typed capability profile Arc uses for planning rather than assuming similarly named native tools are interchangeable.",
        options="--json[=legacy|v1]\n--verbose\n--remote NAME\n--refresh        bypass a fresh remote capability cache entry",
        semantics=(
            "Remote capability output is arc.remote-capability/v1 evidence. It reports transport locality, staging requirements, "
            "publication/finalization semantics, and probe provenance/age. SSH same-parent rename is described as atomic only when "
            "the required remote tools were actually probed. rclone moveto remains provider-dependent even when a server-side Move "
            "feature is reported, because that feature alone does not prove atomic replacement semantics."
        ),
        examples="arc backends\narc backends --remote tablet\narc backends --remote cloud --refresh --json",
        see_also="arc-backends(7), arc-formats(7)",
    )
)
_add(
    _command_page(
        "aliases",
        "Inspect the executable aliases declared by Arc's canonical alias registry and whether each alias currently resolves on PATH.",
        options="--json\n--missing",
        semantics=(
            "The alias registry is the source of truth for package console entry points, Devtool packaging expectations, "
            "completion registration, documentation, and executable dispatch. A missing alias on PATH can therefore be "
            "distinguished from a missing registry declaration."
        ),
        examples="arc aliases\narc aliases --missing\narc aliases --json",
        see_also="arc-doctor(1), arc-completion(1)",
    )
)
_add(
    _command_page(
        "doctor",
        "Audit the active Arc package, source checkout, installed console entry points, PATH aliases, configuration, native backends, optional integrations, and generated documentation/completion surfaces.",
        options="--json\n--fix\n--source PATH",
        semantics=(
            "Doctor reports repository/package/runtime drift explicitly. --fix is deliberately bounded: it refreshes "
            "generated registry/documentation/completion surfaces and reinstalls the selected source checkout editable; "
            "it does not silently install operating-system backends or rewrite user configuration."
        ),
        examples="arc doctor\narc doctor --json\narc doctor --fix --source ~/Code/arc-cli",
        see_also="arc-aliases(1), arc-backends(1), arc-config(5)",
    )
)
_add(
    _command_page(
        "explain",
        "Plan an Arc operation through its real dry-run path and show the decisions and native stages without mutating archives, destinations, transaction state, or remote content.",
        options="--json",
        semantics=(
            "Explain forces the nested operation into dry-run mode. The plan records format/backend selection, typed verification-policy negotiation, publication policy, locality/staging decisions, and source-removal policy where applicable. "
            "It is diagnostic evidence, not a promise that external state will remain unchanged between planning and execution."
        ),
        examples="arc explain convert a.zip -tzst\narc explain --json create backup.tar src/",
        see_also="arc-convert(1), arc-recover(1), arc-backends(7)",
    )
)
_add(
    _command_page(
        "recover",
        "Inspect durable mutation journals and clean transaction-owned temporary paths left by failed or interrupted operations.",
        options="TRANSACTION_ID\n--cleanup\n--all\n--json",
        semantics=(
            "Mutating create/add/update/remove/convert commands record phase evidence under Arc's state directory. Recovery cleanup is deliberately conservative: it removes only exact paths registered as transaction-owned temporary state and never rolls back an already-published destination automatically. "
            "Resumable conversion work is continued with arc convert --resume, using the batch manifest's source/destination verification evidence."
        ),
        examples="arc recover\narc recover TXID --json\narc recover TXID --cleanup",
        see_also="arc-convert(1), arc-explain(1)",
    )
)

_add(
    _command_page(
        "schema",
        "Print the bundled JSON Schemas that define Arc's stable machine envelope, backend/verification contracts, and archive provenance/diff records.",
        options="--list\nmachine-v1 | backend-capability-v1 | verification-evidence-v1 | logical-fingerprint-v1 | archive-diff-v1",
        semantics=(
            "Schemas are shipped as package data and are the public validation contract for --json=v1 consumers. "
            "Bare --json remains the compatibility surface and is intentionally not covered by the versioned envelope schema."
        ),
        examples="arc schema --list\narc schema machine-v1\narc schema logical-fingerprint-v1\narc schema archive-diff-v1",
        see_also="arc(1), arc-backends(1), arc-test(1)",
    )
)

_add(
    _command_page(
        "completion",
        "Generate Zsh completion and inspect, refresh, or clear Arc's remote completion cache.",
        options="arc completion zsh\narc completion cache [--json]\narc completion refresh LOCATION\narc completion clear-cache [LOCATION] [--json]",
        semantics="The generated Zsh completion registers all installed executable aliases and resolves them to canonical Arc commands before asking the hidden completion protocol for candidates.",
        examples="arc completion zsh > completions/_arc\narc completion cache --json",
    )
)

for page in (
    ManualPage(
        "arc-config",
        5,
        "ARC-CONFIG",
        (
            ("NAME", "arc-config - Arc TOML configuration"),
            ("DESCRIPTION", "Arc reads ~/.config/arc/config.toml, or $XDG_CONFIG_HOME/arc/config.toml. Configuration supplies backend preference lists, UI defaults, remote definitions, completion cache policy, and named profiles."),
            ("BACKENDS", "[backends] keys correspond to normalized roles such as tar, zip_create, zip_extract, 7z, rar_create, rar_extract, gzip, bzip2, xz, and zstd. ARC_BACKEND_* environment variables override configured lists."),
            ("REMOTES", "[remotes.NAME] may define type='ssh' with host/user/port/identity_file/proxy_jump/ssh_args, or type='rclone' with a provider remote name."),
            ("UI", "[ui] may set progress, show_native, and native_command_style."),
            ("SEE ALSO", "arc(1), arc-profiles(5), arc-remote(7), arc-backends(7)"),
        ),
    ),
    ManualPage(
        "arc-profiles",
        5,
        "ARC-PROFILES",
        (
            ("NAME", "arc-profiles - reusable Arc option profiles"),
            ("DESCRIPTION", "Profiles live under [profiles.NAME]. Scalar defaults apply before explicit CLI options, and profile filter rules run before command-line rules so the CLI remains final last-match-wins authority."),
            ("FIELDS", "backend, progress, level, threads, yazi, show_native, native_style, execution, include, exclude, include_from, exclude_from."),
            ("EXAMPLE", "[profiles.backup]\nlevel = 8\nthreads = 0\nexclude = ['.git/', '__pycache__/']\n\narc create backup -tzst src/ --profile backup"),
            ("SEE ALSO", "arc(1), arc-config(5)"),
        ),
    ),
    ManualPage(
        "arc-formats",
        7,
        "ARC-FORMATS",
        (
            ("NAME", "arc-formats - normalized archive and stream format semantics"),
            ("FORMATS", "Containers: TAR, ZIP, 7z, RAR. Compressed TAR: gzip, bzip2, xz, Zstandard. Single streams: gzip, bzip2, xz, Zstandard."),
            ("SELECTORS", _FORMAT_LINES),
            ("DETECTION", "Read-side detection prefers content signatures and nested-TAR evidence. Extensions are hints/fallbacks. Empty compressed TAR requires the .tar.* hint because an empty TAR marker is byte-ambiguous with an arbitrary zero-filled stream."),
            ("SINGLE STREAMS", "Single-stream formats represent exactly one byte stream and have no member list. Conversion from a multi-member container to a single stream therefore requires exactly one selected regular file."),
            ("SEE ALSO", "arc(1), arc-create(1), arc-convert(1)"),
        ),
    ),
    ManualPage(
        "arc-backends",
        7,
        "ARC-BACKENDS",
        (
            ("NAME", "arc-backends - native backend resolution and capabilities"),
            ("DESCRIPTION", "Arc resolves a normalized operation to an installed compatible backend using a typed capability profile: operation support, stream I/O, encryption, solid/multipart behavior, random access, metadata, mutation, verification depth, remote suitability, thread support, and safe-index semantics."),
            ("ROLES", "TAR/bsdtar, ZIP create/extract, 7z/7zz, RAR create/extract, and compressor families gzip/bzip2/xz/zstd. Legacy capability strings remain a compatibility projection of the typed profile rather than an independent authority."),
            ("VERIFICATION", "Each backend declares the verification levels it can prove: none, structure, members, and/or full. structure means a readable container/member index (streams satisfy it with a stronger native full test); members adds Arc's normalized member path/type safety validation and is not a content checksum; full runs a backend integrity test that consumes encoded data. An explicit request cannot silently degrade. A weaker available level requires --allow-verification-downgrade and produces downgraded=true evidence."),
            ("DIAGNOSTICS", "arc backends --verbose shows the human capability summary; --json exposes the versioned capability profile. --show-command shows immediate native argv; --show-native records the broader execution plan."),
            ("SEE ALSO", "arc(1), arc-formats(7)"),
        ),
    ),
    ManualPage(
        "arc-remote",
        7,
        "ARC-REMOTE",
        (
            ("NAME", "arc-remote - SSH and rclone transport semantics"),
            ("LOCATIONS", "SSH: ssh://NAME/path or configured NAME:path. rclone: rclone://NAME/path or remote:path."),
            ("EXECUTION", "auto/local uses Arc's conservative stream-or-stage transport. --execution=remote delegates supported operations to Arc installed on the same SSH endpoint after version/capability checks."),
            ("PUBLICATION", "Uploads use a temporary remote object and a finalizer, but the guarantee is transport evidence rather than a generic atomic label. SSH uses a same-parent temporary file plus mv and claims same-filesystem rename atomicity only when the required shell tools were actually probed. rclone uses a temporary object plus moveto; even provider Move=true remains provider-dependent because rclone may implement moves with provider-specific semantics and the feature does not prove atomic replacement."),
            ("CAPABILITY EVIDENCE", "arc backends --remote NAME emits arc.remote-capability/v1 evidence covering locality, staging, publication, the typed remote Arc backend inventory when available, and probe provenance. Cached capability evidence reports source=cache, age, TTL, and provider-generation identity. --refresh bypasses a fresh capability cache entry."),
            ("TRUTHFUL STRATEGIES", "When conversion stages a remote source or destination locally, human and JSON strategy text says transport-staged. Dry-run planning never adds a network probe merely to upgrade a publication claim; absent fresh cached evidence, the guarantee remains unproven/provider-dependent."),
            ("COMPLETION", "Remote directory listings are cached separately by provider/directory/config generation and invalidated by successful mutations."),
            ("SEE ALSO", "arc(1), arc-config(5), arc-convert(1)"),
        ),
    ),
):
    _add(page)


DEFAULT_TOPIC_SECTIONS: dict[str, int] = {
    "arc": 1,
    "identify": 1,
    "create": 1,
    "extract": 1,
    "list": 1,
    "info": 1,
    "diff": 1,
    "test": 1,
    "convert": 1,
    "add": 1,
    "update": 1,
    "remove": 1,
    "backends": 1,
    "aliases": 1,
    "doctor": 1,
    "explain": 1,
    "recover": 1,
    "schema": 1,
    "completion": 1,
    "formats": 7,
    "remote": 7,
    "config": 5,
    "profiles": 5,
}


def manual_topic(topic: str | None) -> ManualPage:
    raw = (topic or "arc").strip()
    if raw in {"overview", "arc"}:
        return PAGES[("arc", 1)]
    if raw.endswith(")") and "(" in raw:
        name, _, section_text = raw[:-1].rpartition("(")
        try:
            section = int(section_text)
        except ValueError:
            section = -1
        if name in DEFAULT_TOPIC_SECTIONS:
            name = "arc" if name == "arc" else f"arc-{name}"
        elif not name.startswith("arc-"):
            name = f"arc-{name}"
        page = PAGES.get((name, section))
        if page is not None:
            return page
    command = canonical_command(raw)
    if command is not None:
        key = (f"arc-{command}", DEFAULT_TOPIC_SECTIONS.get(command, 1))
        page = PAGES.get(key)
        if page is not None:
            return page
    if raw.startswith("arc-"):
        command = canonical_command(raw)
        if command is not None:
            page = PAGES.get((f"arc-{command}", DEFAULT_TOPIC_SECTIONS.get(command, 1)))
            if page is not None:
                return page
    if raw in DEFAULT_TOPIC_SECTIONS:
        name = "arc" if raw == "arc" else f"arc-{raw}"
        page = PAGES.get((name, DEFAULT_TOPIC_SECTIONS[raw]))
        if page is not None:
            return page
    raise KeyError(topic)


def available_topics() -> list[str]:
    return sorted(DEFAULT_TOPIC_SECTIONS)


def _roff_escape(line: str) -> str:
    line = line.replace("\\", r"\\").replace("-", r"\-")
    if line.startswith((".", "'")):
        line = r"\&" + line
    return line


def roff_text(page: ManualPage) -> str:
    out = [f'.TH "{page.title}" "{page.section}" "2026-09-27" "arc-cli" "Arc Manual"']
    for heading, body in page.sections:
        out.append(f'.SH "{heading}"')
        for index, line in enumerate(body.splitlines() or [""]):
            if not line:
                out.append(".PP")
                continue
            if line.startswith("  "):
                out.extend([".nf", _roff_escape(line[2:]), ".fi"])
                continue
            if index > 0:
                out.append(".PP")
            out.append(_roff_escape(line))
    return "\n".join(out) + "\n"


def plain_text(page: ManualPage) -> str:
    lines = [f"{page.title}({page.section})", ""]
    for heading, body in page.sections:
        lines.append(heading)
        lines.extend(f"  {line}" if line else "" for line in body.splitlines())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generated_pages() -> Iterable[tuple[ManualPage, str]]:
    for page in sorted(PAGES.values(), key=lambda item: (item.section, item.name)):
        yield page, roff_text(page)


def markdown_reference() -> str:
    lines = [
        "# Arc command and manual reference",
        "",
        "<!-- Generated by scripts/generate_command_docs.py. Do not edit by hand. -->",
        "",
        "This reference is generated from the same command metadata and structured manual model used by `arc --help`, executable alias dispatch, completion, and `arc man`/`arc help`.",
        "",
    ]
    for page in sorted(PAGES.values(), key=lambda item: (item.section, item.name)):
        lines += [f"## {page.name}({page.section})", ""]
        for heading, body in page.sections:
            lines += [f"### {heading.title()}", "", body, ""]
    return "\n".join(lines).rstrip() + "\n"
