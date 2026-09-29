# ARC COMP-X01 — version authority and composition foundation

Status: **IMPLEMENTED / targeted validation required**

COMP-X01 starts the post-R01–R12 composition wave without changing archive contents or adding user-facing `merge`, `split`, or `join` execution yet.

## Delivered promises

- Arc advances from `0.1.0` to `0.2.0`.
- `src/arc_cli/_version.py::VERSION` is the single authored version literal.
- setuptools reads package metadata dynamically from that authority.
- `arc_cli.__version__`, `arc --version`, doctor/diagnostics, package metadata, distribution tests, and editable-install tests consume or mirror the same authority.
- `uv.lock` mirrors `0.2.0` as generated lock metadata rather than defining runtime version truth.
- `src/arc_cli/composition.py` establishes typed `CompositionSource` / `CompositionOutput` contracts.
- Merge output inference reuses `formats.py` canonical parsing/suffix authority.
- Explicit output suffix and `--format` disagreement fails closed.
- Mixed input formats never trigger an implicit output-format choice.
- Uniform inputs preserve their format by default and derive `<first>.merged<canonical-extension>` when no output is supplied.
- Output names without a recognized archive suffix receive the resolved canonical extension.
- No concat/repack strategy is selected in COMP-X01; that remains COMP-X02 ownership.
- First-class `comp_x01` Devtool profile/workflow/test/contract and `./devtoolw comp-x01` wrapper ownership are included.

## Boundary

COMP-X01 deliberately does **not** register `merge`, `split`, or `join` as CLI commands. That keeps command UX, machine schemas, backend execution, publication policy, and remote behavior out of the foundation overlay until their owning implementation boundary.

The historical `ARC-R01-R12-FINAL` seal remains evidence for the closed pre-0.2 composition baseline. Post-seal development is qualified by the COMP roadmap and will receive its own separate `COMP-G1` gate rather than retroactively redefining that historical seal.
