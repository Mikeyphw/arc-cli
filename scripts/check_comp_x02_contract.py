#!/usr/bin/env python3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
checks={
 'command': ('src/arc_cli/command_docs.py','"merge": CommandDoc'),
 'parser': ('src/arc_cli/cli.py','--member-conflict'),
 'strategy': ('src/arc_cli/cli.py','safe same-format stream concatenation'),
 'resolver': ('src/arc_cli/composition.py','resolve_merge_output'),
 'tests': ('tests/test_comp_x02_merge.py','test_merge_gzip_auto_concat_is_zero_reencode'),
 'audit': ('docs/ARC-COMP-X02-AUDIT.md','COMP-X02'),
 'workflow': ('.devtool.toml','comp_x02'),
 'wrapper': ('docs/WRAPPER.md','comp-x02'),
}
missing=[]
for name,(path,needle) in checks.items():
    p=ROOT/path
    if not p.is_file() or needle not in p.read_text(encoding='utf-8', errors='replace'):
        missing.append(f'{name}: {path} missing {needle!r}')
if missing:
    raise SystemExit('\n'.join(missing))
print('COMP-X02 contract: PASS')
