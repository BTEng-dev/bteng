# Changelog

## 0.3.2 (unreleased)

First release from the relaunched repository. No functional change from 0.3.1 — the
engine, the node library, and the 562-test suite are carried over unchanged.

### Documentation is plain markdown

MkDocs and its theme/plugin stack are gone. `docs/` is read directly from the
repository and renders on GitHub; a separate project website is planned. The
`[docs]` optional-dependency group, `mkdocs.yml`, `requirements-docs.txt` and the
gh-pages deploy workflow were all removed. Markdown that only rendered under MkDocs
was converted: mkdocstrings `:::` directives became symbol tables linking to the
defining module, and `!!!` admonitions became GitHub alert blockquotes.

### Licensing is documented in the package

`bteng/licenses/` ships two reports as package data, so they are readable from an
installed copy without visiting GitHub:

```bash
bteng licenses                 # third-party audit
bteng licenses alternatives    # open-source alternatives survey
bteng licenses --path          # location on disk
```

Both carry the date their facts were verified from primary sources.

### Clearer names on the mock nodes

Two mock methods shared a name but took incompatible types, and one tick counter was a
method where the equivalent on `TreeNode` is a property. Both are renamed:

| Old | New | Why |
|-----|-----|-----|
| `MockActionNode.set_result(NodeStatus)` | `set_status(NodeStatus)` | the old name collided with the condition mock's, which takes a `bool` |
| `MockConditionNode.set_result(bool)` | `set_bool(bool)` | same collision, from the other side |
| `mock.tick_count_local()` | `mock.tick_count_local` | now a property, matching `node.tick_count` |

**Nothing breaks.** The old names still work and emit a `DeprecationWarning`; the counter
returns a value that can still be called like a method. Both forms will be removed in a
later release.

### A pytest plugin so blackboards stop leaking between tests

`Blackboard.create(name)` returns a process-wide singleton, so state written by one test
is visible to the next unless it is cleared by hand. That made leaked state the default.

BTEng now ships fixtures for it. One line in `conftest.py`:

```python
pytest_plugins = ["bteng.testing.plugin"]
```

- an autouse fixture clears every named blackboard before and after each test
- `bteng_blackboard` provides a throwaway blackboard scoped to a single test
- `Blackboard.reset_all()` and `Blackboard.registered_names()` are new public
  classmethods for doing the same thing outside pytest

`pytest` remains a `[dev]` extra only — `bteng.testing.plugin` is the sole module that
imports it, and nothing in the runtime path touches that module.

### Documentation corrected against the code

Every Python snippet in `docs/` was extracted and executed against the built package.
The sweep found API claims that had never been run:

- **`.node()` was documented wrong in 16 places across 6 pages.** The docs showed
  `.node("Nav", Navigate)` — passing a node class. The real signature is
  `.node(type_name, node_name="", attrs=None)`: it resolves a node by its *registered*
  type name, so the documented form raised `KeyError: Unknown node type`. All 16 sites now
  register the class with `@register_node` and refer to it by name.
- **`.timeout(seconds)`** is `.timeout(msec)` — milliseconds, not seconds.
- **`.rate_controller(hz)`** was listed as a `TreeBuilder` method. It does not exist;
  reach `RateController` through the factory or XML.
- **`logger.add_file_sink()`** does not exist. The method is `add_json_file_sink()`.

98 self-contained snippets now run clean; the remaining failures are illustrative
fragments that depend on earlier code on their page.

### Known gaps in this release

Stated plainly rather than left for someone to discover:

| Area | State |
|------|-------|
| Test coverage | 83% overall, 562 tests |
| `bteng/cli.py` | **0% — no test exercises the `bteng` command.** It is verified by hand only |
| `bteng/testing/test_framework.py` | 42% |
| `bteng/core/tree_builder.py` | 71% |
| CI | Runs the suite on 3.12 and 3.13; no coverage floor is enforced |

The engine core is well covered; the gaps are in the command-line entry point and in the
testing helpers. Raising them is planned, not done.

### `py.typed` is no longer shipped

The marker file told downstream type checkers to trust BTEng's annotations as a contract.
Nothing verified them: mypy reports 28 errors across 10 modules, mostly attributes typed
only in a comment (`self._thread_pool = None  # ThreadPool`) rather than in code.

The annotations themselves are unchanged — roughly 94% of the public API carries them and
they remain useful for editors and autocomplete. What is gone is the *claim* that they are
checked. It will be shipped again once a type checker runs in CI.

### Python 3.12 or newer

`requires-python` is now `>=3.12`, raised from `>=3.9`. The engine uses no 3.12-only
syntax — this is a support decision, not a technical one: fewer interpreters to stand
behind means the ones that are claimed can actually be verified.

If your system Python is older (Ubuntu 22.04 and ROS 2 Humble ship 3.10), install a
newer interpreter alongside it and build the virtual environment from that. See
[Install](start-here/install.md).

### `[zmq]` extra requires pyzmq >= 26

Raised from `>=24`. Versions 24.x and 25.x predate pyzmq's relicense and still carry
`LGPL+BSD` metadata; 26.0.0 is the first release where pyzmq is fully BSD-3-Clause
and its bundled libzmq is MPL-2.0. There is now no resolution path by which
installing BTEng pulls LGPL code.

---

## Earlier releases

BTEng 0.2.x and 0.3.0–0.3.1 were published from the previous repository, which is no
longer used. Those releases remain installable from PyPI:
<https://pypi.org/project/bteng/#history>

Two things are worth knowing about them:

- **0.3.0 relicensed the project to MIT.** Releases before it are not MIT.
- **0.2.8 shipped with contradictory license metadata** — its bundled `LICENSE` file
  is Apache-2.0 while its `PKG-INFO` declares `LicenseRef-BTEng-Proprietary`. Use
  0.3.0 or later, where the two agree and both say MIT. Full detail in
  `bteng/licenses/THIRD_PARTY_LICENSES.md` §1b, or run `bteng licenses`.
