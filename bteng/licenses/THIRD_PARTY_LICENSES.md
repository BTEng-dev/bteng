# BTEng — Third-Party License Audit

**Project:** BTEng 0.3.2 · **Own license:** MIT (`LICENSE`, © 2026 BTEng)

> [!NOTE]
> **Scan date: 2026-08-23.** This is a point-in-time snapshot. Every version, license, and
> relicensing date below was read from a primary source on that day — the PyPI JSON API, the GitHub
> REST API, and upstream `LICENSE` files — not from recollection. Method in §8.
>
> Upstream projects can relicense at any time, so re-run the scan before relying on this for a legal
> review. A relicense only ever affects *future* releases: a version already published under a
> permissive license stays permissive forever.
>
> This file ships inside the installed package. Print it with `bteng licenses`.

---

## 1. Headline finding

**The BTEng engine itself has ZERO runtime dependencies.**

`pyproject.toml` declares `dependencies = []`. Every module under `bteng/` imports only the Python
standard library: `abc`, `argparse`, `asyncio`, `collections`, `concurrent`, `copy`, `dataclasses`,
`enum`, `hashlib`, `importlib`, `inspect`, `json`, `logging`, `os`, `queue`, `re`, `sys`, `threading`,
`time`, `types`, `typing`, `uuid`, `weakref`, `xml`.

The only non-stdlib import inside `bteng/` is `zmq`, and it is a **lazy, guarded import** inside
`bteng/introspection/zmq_publisher.py` (lines 36, 106) behind the optional `[zmq]` extra.

**Consequence:** `pip install bteng` pulls no third-party code. Everything in the tables below is
**optional**, **build-time**, or **development/docs-only**. No copyleft obligation reaches an end
user who just installs and imports BTEng.

---

## 1b. BTEng's own license history

The sections below audit *third-party* code. This one records BTEng's own licensing, because it
changed mid-project and that affects anyone who installed an early release.

| Release | Date | `LICENSE` file | Package metadata (`License-Expression`) | Published to PyPI |
|---------|------|----------------|------------------------------------------|-------------------|
| 0.2.7 | 2026-05-13 | Apache-2.0 | `LicenseRef-BTEng-Proprietary` | No |
| **0.2.8** | 2026-05-14 | **Apache-2.0** | **`LicenseRef-BTEng-Proprietary`** | **Yes** |
| 0.2.9 | 2026-05-17 | Apache-2.0 | `LicenseRef-BTEng-Proprietary` | No |
| 0.3.0 | 2026-07-15 | **MIT** | **`MIT`** | Yes |
| 0.3.1 | 2026-07-28 | MIT | `MIT` | Yes |
| 0.3.2 | — | MIT | `MIT` | Not yet |

> [!WARNING]
> **0.2.8 is internally contradictory and should not be relied on.**
> It is the only pre-MIT release on PyPI, and its two licensing statements disagree: the bundled
> `LICENSE` file is the full Apache License 2.0, while the metadata declares
> `LicenseRef-BTEng-Proprietary`. Verified by downloading the published artifact from PyPI on
> 2026-08-23 and reading both files inside it — not inferred from the repository.
>
> **Upgrade to 0.3.0 or later**, where the `LICENSE` file and the metadata agree and both say MIT.

Notes on the relicense (commit `6172977`, 2026-07-15):

* **Copyright was never in doubt.** Every commit in the project's history is by a single author, so
  no third-party contributor permission was needed to relicense.
* **A relicense only binds future releases.** Anyone who obtained 0.2.8 keeps whatever rights that
  release granted them; MIT applies from 0.3.0 onward. This is why re-releasing or deleting old
  versions does not "undo" an earlier license, and why deleting them is not a fix.
* **Apache-2.0 → MIT drops the express patent grant.** Apache-2.0 §3 grants patent rights
  explicitly; MIT does not. MIT releases therefore offer slightly weaker patent protection than the
  Apache-2.0 `LICENSE` file did. A deliberate trade for MIT's brevity and ubiquity.

---

## 2. Direct dependencies — license, current version, change history

| # | Library | Role in BTEng | Declared constraint | Current version (2026-08-23) | Current license | License ever changed? | From → To | Version / date of change | Open source? |
|---|---------|---------------|--------------------|------------------------------|-----------------|----------------------|-----------|--------------------------|--------------|
| 1 | **pyzmq** | Optional extra `[zmq]` — `ZmqPublisher` introspection transport | `pyzmq>=26` | **27.2.0** (2026-08-20) | **BSD-3-Clause** | ✅ **Yes, twice** | `LGPL+BSD` dual → `BSD-3-Clause` | **26.0.0** (2024-04). Earlier partial step at **2.2.0** (2012) | ✅ Yes — OSI-approved |
| 2 | **libzmq** (C lib bundled inside pyzmq wheels) | Transitive native lib of pyzmq | via pyzmq wheel | **4.3.5+** | **MPL-2.0** | ✅ **Yes** | `LGPL-3.0+ (with custom static-link exceptions)` → `MPL-2.0` | **libzmq 4.3.5**, released 2023-10-09 | ✅ Yes — OSI-approved (FSF-free, weak copyleft) |
| 3 | **setuptools** | Build backend (`build-system.requires`) | `setuptools>=77` | **84.0.0** (2026-08-08) | **MIT** | ✅ **Yes** | `PSF or ZPL` (dual) → `MIT` | **19.5** (2016-01-24; relicense commit `d0bd7a56`, 2016-01-23) | ✅ Yes — OSI-approved |
| 4 | **wheel** | Build backend (`build-system.requires`) | `wheel` (unpinned) | **0.48.0** (2026-08-11) | **MIT** | ❌ No | — | MIT since 0.1 | ✅ Yes — OSI-approved |
| 5 | **pytest** | `[dev]` extra — test suite | `pytest` (unpinned) | **9.1.1** (2026-06-19) | **MIT** | ❌ No | — | MIT for the whole published history | ✅ Yes — OSI-approved |
| 6 | **build** (`pypa/build`) | `[dev]` extra — sdist/wheel builder | `build` (unpinned) | **1.5.0** (2026-04-30) | **MIT** | ❌ No | — | MIT since 0.0.2 | ✅ Yes — OSI-approved |
| 7 | **twine** | `[dev]` extra — PyPI upload | `twine` (unpinned) | **7.0.0** (2026-07-27) | **Apache-2.0** | ❌ No | — | Apache-2.0 since 1.2.0 | ✅ Yes — OSI-approved |
| 8 | **mkdocs** | `[docs]` extra + `requirements-docs.txt` | `mkdocs>=1.5` | **1.6.1** (2024-08-30) | **BSD-2-Clause** | ❌ No | — | BSD since 0.10 | ✅ Yes — OSI-approved |
| 9 | **mkdocs-material** | `[docs]` extra — docs theme (`mkdocs.yml`) | `mkdocs-material>=9.5` | **9.7.7** (2026-07-17) | **MIT** | ❌ No | — | MIT since 0.1.0 ⚠️ see note | ✅ Yes (public edition) — ⚠️ *Insiders* edition is proprietary sponsorware |
| 10 | **mkdocstrings** | `[docs]` extra — API reference plugin | `mkdocstrings[python]>=0.24` | **1.0.6** (2026-07-11) | **ISC** | ❌ No | — | ISC since 0.2.0 | ✅ Yes — OSI-approved |
| 11 | **mkdocstrings-python** | Pulled by the `[python]` extra above | via `mkdocstrings[python]` | **2.0.7** (2026-08-17) | **ISC** | ❌ No | — | ISC since first release | ✅ Yes — OSI-approved |
| 12 | **graphviz** (Python pkg, xflr6) | `docs/images/generate.py` — diagram generation | ⚠️ **undeclared** | **0.21** (2025-06-15) | **MIT** | ❌ No | — | MIT since 0.4 | ✅ Yes — OSI-approved |
| 13 | **Graphviz** (C tool, `dot` binary) | Native backend the Python pkg shells out to | system package | **16.0.0** (2026-08-14) | **EPL-2.0** | ✅ **Yes** | `EPL-1.0` → `EPL-2.0` | commit `8cd2e55b` 2026-03-07 → first shipped in **14.1.4** (2026-03-21). Historically `CPL-1.0` → `EPL-1.0` around 2011 (pre-dates the current repo, not verifiable in-tree) | ✅ Yes — OSI-approved (weak copyleft) |
| 14 | **CPython** | Interpreter | `requires-python = ">=3.9"` | 3.12.3 (this machine) | **PSF License 2.0** | ✅ Historically | `CNRI` / `CWI` → `PSF-2.0` | PSF-2.0 since Python 2.1/2.0.1 (2001) | ✅ Yes — OSI-approved, GPL-compatible |

---

## 3. Transitive dependencies (docs toolchain only)

Pulled in only by `pip install -r requirements-docs.txt` / `pip install ".[docs]"`. Never shipped
in the BTEng wheel.

| Library | Pulled in by | Current version | License | Change history | Open source? |
|---------|--------------|-----------------|---------|----------------|--------------|
| **griffe** | mkdocstrings-python | 2.2.0 | ISC | No change | ✅ Yes — OSI-approved |
| **mkdocs-autorefs** | mkdocstrings | 1.4.4 | ISC | No change | ✅ Yes — OSI-approved |
| **pymdown-extensions** | mkdocs-material (+ used directly in `mkdocs.yml`: `pymdownx.details`, `superfences`, `highlight`, `inlinehilite`, `tabbed`, `emoji`) | 11.0.2 | MIT | No change | ✅ Yes — OSI-approved |
| **mkdocs-material-extensions** | mkdocs-material | 1.3.1 | MIT | No change | ✅ Yes — OSI-approved |
| **Jinja2** | mkdocs | 3.1.6 | BSD-3-Clause | No change | ✅ Yes — OSI-approved |
| **Markdown** | mkdocs | 3.10.3 | BSD-3-Clause | No change | ✅ Yes — OSI-approved |
| **MarkupSafe** | Jinja2 | 3.0.3 | BSD-3-Clause | No change | ✅ Yes — OSI-approved |
| **Pygments** | mkdocs-material | 2.21.0 | BSD-2-Clause | No change | ✅ Yes — OSI-approved |
| **Babel** | mkdocs-material | 2.18.0 | BSD-3-Clause | No change | ✅ Yes — OSI-approved |
| **colorama** | mkdocs-material | 0.4.6 | BSD-3-Clause | No change | ✅ Yes — OSI-approved |
| **paginate** | mkdocs-material | 0.5.7 | MIT | No change | ✅ Yes — OSI-approved |
| **requests** | mkdocs-material | 2.34.2 | **Apache-2.0** | ✅ **Changed:** `ISC` → `Apache-2.0` at **requests 1.0.0** (2012-12) | ✅ Yes — OSI-approved |
| **click** | mkdocs | 8.4.2 | BSD-3-Clause | No change | ✅ Yes — OSI-approved |
| **PyYAML** | mkdocs | 6.0.3 | MIT | No change | ✅ Yes — OSI-approved |
| **pyyaml-env-tag** | mkdocs | 1.1 | MIT | No change | ✅ Yes — OSI-approved |
| **watchdog** | mkdocs | 6.0.0 | Apache-2.0 | No change | ✅ Yes — OSI-approved |
| **ghp-import** | mkdocs | 2.1.0 | Apache-2.0 | No change | ✅ Yes — OSI-approved |
| **mergedeep** | mkdocs | 1.3.4 | MIT | No change | ✅ Yes — OSI-approved |
| **platformdirs** | mkdocs | 4.11.3 | MIT | No change | ✅ Yes — OSI-approved |
| **pathspec** | mkdocs | 1.1.1 | **MPL-2.0** ⚠️ | No change — but note it is weak copyleft (file-level); build-tool only, never linked or redistributed | ✅ Yes — OSI-approved (weak copyleft) |

---

## 4. CI / non-Python components

| Component | Where | License | Open source? |
|-----------|-------|---------|--------------|
| `actions/checkout@v4` | `.github/workflows/docs.yml` | MIT | ✅ Yes — OSI-approved |
| `actions/setup-python@v5` | `.github/workflows/docs.yml` | MIT | ✅ Yes — OSI-approved |


**Open-source status: 100%.** All 37 components audited above are open source under an
**OSI-approved** license. There is no proprietary, source-available, "fair-source", commercial, or
custom-EULA component anywhere in BTEng's runtime, build, dev, docs, or CI chain. The single
proprietary product adjacent to the stack — *Material for MkDocs Insiders* — is **not** used;
BTEng consumes the public MIT-licensed `mkdocs-material` package.

Licenses in play, all OSI-approved: MIT, BSD-2-Clause, BSD-3-Clause, ISC, Apache-2.0, MPL-2.0,
EPL-2.0, PSF-2.0. Three are weak copyleft (MPL-2.0 on `libzmq` and `pathspec`, EPL-2.0 on the
Graphviz `dot` binary); the remaining are permissive.

---

## 5. The three real license-change stories

### 5.1 pyzmq: LGPL+BSD → BSD-3-Clause (v26.0.0, April 2024)

The most material change for BTEng, because `pyzmq` is the only third-party library that can end up
in a BTEng *runtime* environment.

* **Before v26.0.0:** PyPI metadata read `LGPL+BSD` with both `BSD License` and
  `GNU Library or Lesser General Public License (LGPL)` classifiers. The Python layer was BSD, but
  the Cython `zmq.core` backend and the bundled `libzmq` carried LGPL.
* **Partial step, v2.2.0 (2012):** changelog states *"all code outside `zmq.core` is BSD licensed
  (where possible), to allow more permissive use of less-critical code and utilities."*
* **v26.0.0 (2024-04):** the Cython backend was rewritten in Cython 3 pure-Python mode and, per the
  changelog, *"pyzmq's Cython backend is now BSD-licensed, matching the rest of pyzmq."* PyPI
  metadata switched to full BSD-3-Clause text at exactly this version.
* Bundled `libzmq` was simultaneously bumped to 4.3.5, whose license is **MPL-2.0**.

**Impact on BTEng — resolved.** BTEng previously declared `pyzmq>=24`, which allowed resolving to
the LGPL-era 24.x and 25.x releases. The constraint is now **`pyzmq>=26`**, so the `[zmq]` extra is
unambiguously BSD-3-Clause (pyzmq) plus MPL-2.0 (bundled libzmq). There is no longer any path by
which installing BTEng pulls LGPL code.

### 5.2 libzmq: LGPL-3.0+ → MPL-2.0 (v4.3.5, 2023-10-09)

From libzmq `NEWS`:

> *"Relicensing from LGPL-3.0+ (with custom exceptions) to MPL-2.0 is now complete. libzmq is now
> distributed under the Mozilla Public License 2.0. Relicensing grants have been collected from all
> relevant authors, and some functionality has been clean-room reimplemented where that was not
> possible."*

A multi-year effort (tracked in `zeromq/libzmq#2376`). MPL-2.0 is file-level weak copyleft — far
friendlier for linking into proprietary/commercial products than LGPL-3.0.

### 5.3 setuptools: PSF-or-ZPL → MIT (v19.5, January 2016)

Relicense commit (2016-01-23): *"Relicense the package as MIT license. Drop licensing as PSF and
Zope as neither of those…"*. PyPI metadata confirms: 19.4 = `PSF or ZPL`; 19.5 = MIT classifier.
BTEng requires `setuptools>=77`, so it is far past this boundary — historical interest only.

Separately, **setuptools 77.0.0** added PEP 639 SPDX license-expression support. That is what lets
BTEng's own `pyproject.toml` write `license = "MIT"` + `license-files = ["LICENSE"]` — a **metadata
format** change, not a license change. It is why the constraint is `>=77`.

---

## 6. Notes and caveats

* ⚠️ **`graphviz` is undeclared.** `docs/images/generate.py` imports it, but it appears in neither
  the `[docs]` extra nor `requirements-docs.txt`. Add `graphviz>=0.20` to the `[docs]` extra if the
  diagram generator is meant to be reproducible. It is MIT, so no license risk — only a
  reproducibility gap.
* ⚠️ **Material for MkDocs Insiders** is a separate **proprietary/sponsorware** distribution. Only
  the public `mkdocs-material` package is MIT. BTEng uses the public MIT package.
* ⚠️ **`pathspec` is MPL-2.0**, and **`libzmq` is MPL-2.0**. Both are weak copyleft. `pathspec` is a
  build-time tool only — never linked, never redistributed. `libzmq` is redistributed inside pyzmq
  wheels; MPL-2.0 obligations attach to modified MPL-covered files only, so simply using the wheel
  is unencumbered.
* ✅ **The pyzmq LGPL window is closed.** The `[zmq]` extra requires `>=26`, the first fully
  BSD-3-Clause release. See §5.1.
* **`[dev]` deps are unpinned** (`build`, `pytest`, `twine`). All three are permissive
  (MIT/MIT/Apache-2.0) with no license change in their history, so an unpinned resolve carries no
  license risk.
* **Dependency set has been stable across BTEng's entire history.** Every commit touching
  `pyproject.toml` (`0df45e9`, `1010a0c`, `547b238`, `6172977`, `b387fc3`) declares the same
  `dependencies = []` plus the same three extras. No dependency has ever been added and removed.
* **BTEng itself relicensed** in commit `6172977` ("Relicense under MIT and bump version to 0.3.0").

---

## 7. Compatibility verdict

| Distribution scenario | Licenses involved | MIT-compatible? |
|-----------------------|-------------------|-----------------|
| `pip install bteng` (core engine) | MIT only + PSF (CPython) | ✅ Yes — nothing else present |
| `pip install "bteng[zmq]"` (requires pyzmq ≥ 26) | MIT + BSD-3-Clause + MPL-2.0 (libzmq) | ✅ Yes |
| `[dev]` environment | MIT, Apache-2.0 | ✅ Yes — not redistributed |
| `[docs]` environment | MIT, BSD-2/3, ISC, Apache-2.0, MPL-2.0 (`pathspec`) | ✅ Yes — build-time only, not redistributed |

**Bottom line:** BTEng ships as clean MIT with **no third-party code in the wheel** — verified by
inspecting the built artifact, not inferred: every file in `bteng-0.3.1-py3-none-any.whl` lives under
`bteng/` or `bteng-0.3.1.dist-info/`, with no vendored or bundled code. Every `Requires-Dist` entry
is guarded by `extra ==`, so a plain `pip install bteng` installs nothing but MIT-licensed code.

A downstream user — including a closed-source commercial one — can depend on BTEng and owes nothing
beyond retaining the MIT notice.

---

## 8. Verification method

Every row was checked against a primary source, not from memory:

* Versions + license metadata per release: PyPI JSON API (`https://pypi.org/pypi/<pkg>/<version>/json`),
  reading both the `license` / `license_expression` fields and `License ::` classifiers.
* Change points were **bisected** version-by-version (e.g. pyzmq 25.1.2 vs 26.0.0; setuptools 19.4 vs 19.5).
* Relicense claims cross-checked against upstream changelogs (`pyzmq/docs/source/changelog.md`,
  `libzmq/NEWS`) and upstream `LICENSE` files.
* setuptools relicense confirmed via GitHub commit search (`d0bd7a56`, 2016-01-23).
* Graphviz EPL-1.0 → EPL-2.0 confirmed via GitLab commit history of its `LICENSE` file
  (`8cd2e55b`, 2026-03-07), mapped to release 14.1.4 (2026-03-21) via the GitLab releases API.
* BTEng's own dependency history read from `git log -- pyproject.toml` across all five commits.
* **BTEng's own published artifacts were downloaded from PyPI and opened** (§1b) — the 0.2.8
  contradiction was found by reading the `LICENSE` file and `PKG-INFO` inside the released sdist,
  not by reading the repository.
* The claim that the wheel contains no third-party code (§7) was checked by listing every entry in
  the built `bteng-0.3.1-py3-none-any.whl` and by installing it into a clean virtualenv.
* Anything that could not be positively confirmed is stated as unconfirmed rather than asserted.
