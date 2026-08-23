# BTEng — Open-Source Alternative Libraries

**Purpose:** candidate libraries for every functional area BTEng touches — today's dependencies and
plausible future ones — restricted to options that are **open source with no usage limits**.

> [!NOTE]
> **Scan date: 2026-08-23.** Every license and version below was read from a primary source on that
> day (PyPI JSON API, GitHub REST API, upstream `LICENSE` files) — see §17. Licenses change; re-scan
> before relying on this for a decision.
>
> Companion document: [THIRD_PARTY_LICENSES.md](../../bteng/licenses/THIRD_PARTY_LICENSES.md), which audits what BTEng
> actually uses today. This file surveys what it *could* use. The audit also ships inside the package — print it with `bteng licenses`.

---

## 0. What "open source and not limited" means here

Every row in this document was filtered against four tests. A library is listed as **✅ Adoptable**
only if it passes all four:

| Test | Requirement |
|------|-------------|
| **License** | OSI-approved. No source-available (RSAL, BSL, SSPL), no "free for non-commercial", no custom EULA. |
| **No usage limit** | No seat caps, no rate limits, no paid tier gating core features, no phone-home, no registration. |
| **Self-hostable** | Runs entirely on your machine. No mandatory SaaS, no API key, no vendor account. |
| **No copyleft reach** | Permissive or weak-copyleft only. Nothing that could impose obligations on BTEng's MIT users. |

Options that fail a test are still listed — in **§15, License traps** — because knowing what to
avoid is half the value, and several are the *obvious* pick in their category.

**Standing constraint:** BTEng's headline property is **zero runtime dependencies** (`dependencies = []`,
`bteng/` imports only the standard library). Nothing in §1–§8 should become a hard runtime dependency.
Anything adopted belongs behind an optional extra with a guarded import — the pattern already used
for `zmq` in `bteng/introspection/zmq_publisher.py`.

**Out of scope:** documentation-site generators. `docs/` is plain markdown read from the repository,
and the project website is handled separately. The two docs-tooling *traps* in §15 (`pdoc3` and
Material for MkDocs Insiders) are kept, because they still apply to anything that generates API
documentation for that website.

**Legend:** 🟢 permissive · 🟡 weak copyleft (file-level; safe to depend on, obligations attach only
to modified files of that library) · 🔴 strong copyleft or restricted — see §15.

---

## 1. Messaging / introspection transport

**Today:** `pyzmq>=24` (BSD-3-Clause) → bundles `libzmq` (MPL-2.0 🟡), behind the `[zmq]` extra.
Used by `bteng/introspection/zmq_publisher.py` to stream tick events to external monitors.

| Library | Version | License | | Why consider it | Trade-off |
|---------|---------|---------|---|-----------------|-----------|
| **stdlib `socket` + `json`** | — | PSF-2.0 | 🟢 | Zero deps. A TCP or UDP line-delimited event stream is ~80 lines and keeps the "no dependencies" promise absolute. | You reimplement reconnect, backpressure, framing, and pub/sub fan-out. |
| **pynng** (nng bindings) | 0.9.0 | MIT | 🟢 | The closest like-for-like swap for ZeroMQ. Same pub/sub, req/rep, pair patterns. Native `nng` core is **MIT** — strictly more permissive than libzmq's MPL-2.0. | Smaller ecosystem than ZeroMQ; fewer language bindings on the monitor side. |
| **websockets** | 17.0.1 | BSD-3-Clause | 🟢 | Browser-native. A monitoring dashboard becomes a static HTML page with no plugin or native client. | Async-first; needs a bridge from BTEng's threaded tick loop. |
| **paho-mqtt** | 2.1.0 | EPL-2.0 OR BSD-3-Clause | 🟢 | Standard in IoT/robotics fleets. Dual-licensed — take the BSD-3 arm and copyleft never enters the picture. | Requires a broker process (Mosquitto, EPL-2.0/BSD dual). |
| **nats-py** | — | Apache-2.0 | 🟢 | Very low latency; the NATS server is Apache-2.0 and a single static binary. | Another daemon to run. |
| **aiohttp** | 3.14.3 | Apache-2.0 AND MIT | 🟢 | HTTP + WebSocket in one, if a REST introspection API is also wanted. | Heavier than the job needs for pure event streaming. |
| **grpcio** | 1.83.0 | Apache-2.0 | 🟢 | Strong typing and streaming, good cross-language monitor clients. | Large native wheel; protobuf toolchain; overkill for a tick stream. |
| **zenoh-python** | — | Apache-2.0 OR EPL-2.0 | 🟢 | Purpose-built for robotics; ROS 2's alternate middleware. Relevant given `bteng-ros2`. | Young ecosystem, larger conceptual surface. |

> **Recommendation — done.** The `[zmq]` extra now requires **`pyzmq>=26`**, the first release where
> the Cython backend is BSD-3-Clause and bundled libzmq is MPL-2.0; 24.x/25.x carried the pre-relicense
> `LGPL+BSD` metadata. If you ever want the native layer permissive end-to-end rather than weak
> copyleft, **pynng** is the swap — nng's core is MIT.

---

## 2. Event serialization

**Today:** stdlib `json`.

| Library | Version | License | | Why consider it | Trade-off |
|---------|---------|---------|---|-----------------|-----------|
| **stdlib `json`** | — | PSF-2.0 | 🟢 | Zero deps, universally readable, debuggable by eye. | Slowest; verbose on the wire. |
| **msgpack** | 1.2.1 | Apache-2.0 | 🟢 | ~2–5× smaller and faster than JSON, decoders in every language — matters for a high tick-rate stream. | Binary; not human-readable in a log. |
| **cbor2** | 6.1.4 | MIT | 🟢 | Standardised (RFC 8949) equivalent of msgpack; pure-Python fallback available. | Slightly less ubiquitous tooling. |
| **msgspec** | 0.21.1 | BSD-3-Clause | 🟢 | Serialization **and** schema validation in one, very fast. Could serve §6 simultaneously. | Requires typed structs; a design commitment. |
| **flatbuffers** | 25.12.19 | Apache-2.0 | 🟢 | Zero-copy reads — a monitor can mmap a trace file. | Schema compiler in the build loop. |
| **protobuf** | 7.36.0 | BSD-3-Clause | 🟢 | Pairs with gRPC; excellent cross-language story. | Codegen step; heavy for the benefit here. |
| ⚠️ **orjson** | 3.12.0 | MPL-2.0 AND (Apache-2.0 OR MIT) | 🟡 | Fastest JSON in Python by a wide margin. | The **MPL-2.0 component is not optional** — the package as a whole is weak copyleft. Fine to depend on, but it is not the "pure permissive" choice its Apache/MIT arm suggests. |

> **Recommendation:** stay on stdlib `json` for the default path. If tick-rate ever makes
> serialization the bottleneck, **msgpack** (Apache-2.0) is the clean swap behind the same extra.

---

## 3. Diagram generation

**Today:** `graphviz` 0.21 (MIT 🟢) in `docs/images/generate.py` — but it shells out to the
**Graphviz `dot` binary (EPL-2.0 🟡)**, an undeclared system requirement. Output is 6 checked-in SVGs.

| Option | Version | License | | Why consider it | Trade-off |
|--------|---------|---------|---|-----------------|-----------|
| **Mermaid** | 11.x | MIT | 🟢 | **GitHub renders ```mermaid fences natively.** No binary, no generate step, no checked-in SVGs — diagrams live in the markdown and stay in sync with it. Strongest fit now that docs are read on GitHub. | Less layout control than Graphviz for dense graphs. |
| **D2** | — | MPL-2.0 | 🟡 | Much nicer default layouts than Graphviz; single Go binary. | Still a build step producing SVGs; weak copyleft on the tool. |
| **pydot** | 4.0.1 | MIT | 🟢 | Drop-in-ish alternative to the `graphviz` package. | Same EPL-2.0 `dot` binary underneath — solves nothing licensing-wise. |
| **networkx** (+ matplotlib) | 3.6.1 | BSD-3-Clause | 🟢 | Pure Python, no native binary. Also gives graph algorithms usable on trees themselves. | Layout quality well below Graphviz for tree diagrams. |
| **diagrams** | 0.25.1 | MIT | 🟢 | Architecture diagrams as Python code. | Requires Graphviz `dot` too. |
| 🔴 **PlantUML** | v1.2026.6 | **LGPL-3.0** | 🔴 | — | Java runtime plus copyleft. See §15. |

> **Recommendation:** **Mermaid.** It removes the only undeclared system dependency in the project
> and the only EPL-2.0 component, deletes `docs/images/generate.py` and its six SVGs, and diagrams
> become reviewable in a diff. Keep the PNG logo as a file.

---

## 4. Live tree monitoring / visual editor

**Today:** none open — `ZmqPublisher` emits events, but there is no bundled viewer. The obvious
consumer in the wider BT world is **Groot2, which is not open source**.

| Option | License | | Why consider it | Trade-off |
|--------|---------|---|-----------------|-----------|
| **Browser dashboard** (websockets + vanilla JS/Mermaid) | BSD-3 + MIT | 🟢 | No install for the user, no GUI toolkit licensing questions at all, cross-platform for free. Mermaid can render the live tree. | You build it. |
| **Textual** | MIT | 🟢 | A TUI tree monitor in the terminal — no browser, no display server, works over SSH. Excellent fit for headless robots. | Terminal-only. |
| **Dear PyGui** | MIT | 🟢 | True GPU-accelerated desktop GUI under a **permissive** license — the rare one in this category. | Immediate-mode API; smaller widget set than Qt. |
| **Flask / FastAPI + Starlette** | BSD-3 / MIT | 🟢 | Serve the dashboard and a REST introspection API from the engine process. | Adds a web server to a control-loop process. |
| **plotly** / **bokeh** | MIT / BSD-3 | 🟢 | Tick-timing and status-history charts. | Heavy for live views. |
| **nodeeditor** (BehaviorTree org) | BSD-3-Clause | 🟢 | A permissively licensed Qt node editor — the buildable base for an open BT editor. | Qt binding licensing applies — see below. |
| 🔴 **Groot2** | **no license file** | 🔴 | — | The `BehaviorTree/Groot2` repository declares **no license**, so default copyright applies: all rights reserved. Not open source. See §15. |
| 🔴 **PyQt5 / PyQt6** | GPL-3.0 or commercial | 🔴 | — | Would force BTEng-derived GUIs to GPL. See §15. |
| 🟡 **PySide6** | LGPL-3.0 OR GPL-2.0 OR GPL-3.0 | 🟡 | The Qt binding you *can* use — LGPL permits dynamic linking from a permissive app. | Relinking obligation; heavy dependency. |

> **Recommendation:** a **websockets + Mermaid browser dashboard** for the desktop case and
> **Textual** for headless. Both are permissive, both avoid the Qt licensing question entirely, and
> both consume the event stream BTEng already produces.

---

## 5. XML parsing

**Today:** stdlib `xml.etree.ElementTree` in `bteng/xml_parser/parser.py`.

| Library | Version | License | | Why consider it | Trade-off |
|---------|---------|---------|---|-----------------|-----------|
| **stdlib `xml.etree`** | — | PSF-2.0 | 🟢 | Zero deps. Sufficient for the BT XML dialect. | No schema validation; no line numbers in errors. |
| **defusedxml** | 0.7.1 | PSF-2.0 | 🟢 | Hardens against XXE / billion-laughs — worth it the moment tree XML can come from an untrusted source. Drop-in API. | Last release 2021 (stable, narrow scope by design). |
| **lxml** | 6.1.2 | BSD-3-Clause | 🟢 | XSD/RelaxNG validation, XPath, precise error positions — would let BTEng ship a real `.xsd` for its dialect. | Native libxml2 build; a significant dependency for a zero-dep engine. |

> **Recommendation:** stay on stdlib. Add **defusedxml** behind an extra if loading third-party trees
> becomes a use case — it is a security question, not a features question.

---

## 6. Port / type validation and data models

**Today:** hand-rolled `PortDefinition`, `NodeContract`, `PortSchema` on stdlib `dataclasses`.

| Library | Version | License | | Why consider it | Trade-off |
|---------|---------|---------|---|-----------------|-----------|
| **stdlib `dataclasses` + `typing`** | — | PSF-2.0 | 🟢 | Current approach. Zero deps, full control over error messages. | You maintain the coercion and validation logic. |
| **attrs** | 26.1.0 | MIT | 🟢 | More powerful than dataclasses (validators, converters, slots) with a tiny footprint. | Still a runtime dependency. |
| **msgspec** | 0.21.1 | BSD-3-Clause | 🟢 | Validation *and* fast serialization in one — could cover §2 and §6 together. | Newer; smaller community. |
| **pydantic** | 2.13.4 | MIT | 🟢 | The de-facto standard; excellent coercion, JSON Schema export for free. | Large dependency (Rust core) — a hard sell against `dependencies = []`. |
| **jsonschema** | 4.26.0 | MIT | 🟢 | Validate tree definitions against a published schema, language-agnostic. | Validates data, not Python types. |
| **cattrs** / **marshmallow** | 26.1.0 / 4.3.1 | MIT | 🟢 | Structuring/unstructuring layers if `attrs` is adopted. | Extra layer. |

> **Recommendation:** **do not adopt any of these into the core.** The zero-dependency property is
> worth more to BTEng's users than the ergonomics gain. If a validation extra is ever wanted,
> **msgspec** buys the most per dependency added.

---

## 7. CLI framework

**Today:** stdlib `argparse` in `bteng/cli.py` (entry point `bteng = "bteng.cli:main"`).

| Library | Version | License | | Why consider it | Trade-off |
|---------|---------|---------|---|-----------------|-----------|
| **stdlib `argparse`** | — | PSF-2.0 | 🟢 | Zero deps. Fine for the current command surface. | Verbose; weak subcommand ergonomics. |
| **click** | 8.4.2 | BSD-3-Clause | 🟢 | Clean decorator API, excellent nested subcommands, shell completion. | Runtime dependency for the CLI extra. |
| **typer** | 0.27.1 | MIT | 🟢 | Type-hints-as-CLI — a natural fit for a fully annotated codebase. Built on click. | Pulls click plus its own layer. |
| **docopt** | 0.6.2 | MIT | 🟢 | CLI defined by its own help text. | **Unmaintained since 2014** — fails the "not limited" spirit on the maintenance axis. |

> **Recommendation:** stay on `argparse` while the CLI is small. **click** if it grows — BSD-3, and
> already battle-tested as the base of `typer`, `flask`, and `black`.

---

## 8. Terminal output / TUI

**Today:** stdlib `logging` and `print`.

| Library | Version | License | | Why consider it | Trade-off |
|---------|---------|---------|---|-----------------|-----------|
| **rich** | 15.0.0 | MIT | 🟢 | Colored tree rendering in the terminal — nearly purpose-built for showing a live behavior tree with per-node status. | Runtime dependency. |
| **textual** | 8.2.8 | MIT | 🟢 | Full TUI app framework from the same authors; the headless monitor option from §4. | Larger surface. |
| **blessed** | 1.48.0 | MIT | 🟢 | Lower-level terminal control, very small. | You build the rendering. |
| 🔴 **urwid** | 4.0.11 | **LGPL-2.1-only** | 🔴 | — | Copyleft; unnecessary when `rich`/`textual` are MIT. See §15. |

> **Recommendation:** **rich** behind a `[cli]` extra for a `bteng inspect` style live tree view.

---

## 9. Testing, coverage, property-based testing

**Today:** `pytest` (MIT 🟢) in the `[dev]` extra. 562 tests passing.

| Library | Version | License | | Why consider it |
|---------|---------|---------|---|-----------------|
| **pytest** | 9.1.1 | MIT | 🟢 | Current. No reason to move. |
| **stdlib `unittest`** | — | PSF-2.0 | 🟢 | Zero-dep fallback if `[dev]` must be dependency-free. |
| **coverage.py** | 7.15.4 | Apache-2.0 | 🟢 | Line/branch coverage; nothing in this project measures it today. |
| **pytest-cov** | 7.1.0 | MIT | 🟢 | The pytest bridge to coverage.py. |
| **pytest-benchmark** | 5.2.3 | BSD-2-Clause | 🟢 | Tick-loop performance regressions are exactly the kind of bug a BT engine should guard against. |
| **hypothesis** | 6.165.10 | MPL-2.0 | 🟡 | Property-based testing — excellent for a state machine like `NodeStatus`/tick lifecycle. Weak copyleft, dev-only, never redistributed → no practical obligation. |

> **Recommendation:** add **pytest-cov** and **pytest-benchmark** to `[dev]`. Consider **hypothesis**
> for tick-lifecycle invariants; MPL-2.0 in a dev-only tool carries no obligation for BTEng's users.

---

## 10. Build backend and packaging

**Today:** `setuptools>=77` + `wheel` (both MIT 🟢); `build` (MIT) and `twine` (Apache-2.0) in `[dev]`.

| Tool | Version | License | | Why consider it | Trade-off |
|------|---------|---------|---|-----------------|-----------|
| **setuptools** | 84.0.0 | MIT | 🟢 | Current. `>=77` is required for the PEP 639 SPDX `license = "MIT"` field already in use. | Large; carries legacy surface. |
| **hatchling** | 1.32.0 | MIT | 🟢 | Modern, small, fast; excellent `pyproject.toml`-only config. The strongest alternative for a pure-Python package. | Migration effort; MANIFEST.in rules must be re-expressed. |
| **flit-core** | 4.0.2 | BSD-3-Clause | 🟢 | The minimal option — near-zero config for a simple pure-Python package like this one. | Least flexible. |
| **pdm-backend** / **poetry-core** | 2.4.9 / 2.4.1 | MIT | 🟢 | Fine if you adopt PDM/Poetry for workflow. | Buys nothing on its own. |
| **uv** | 0.12.5 | MIT OR Apache-2.0 | 🟢 | Dramatically faster installs/resolution; can replace pip+venv+build in CI. | New tool in the chain. |
| **twine** | 7.0.0 | Apache-2.0 | 🟢 | Current uploader. `uv publish` is an alternative. | — |

> **Recommendation:** no change is needed — everything here is already MIT/Apache. If you want a
> smaller build surface, **hatchling** is the clean move. **uv** is a genuine CI speed win at no
> licensing cost.

---

## 11. Lint, format, type-check

**Today:** nothing configured.

| Tool | Version | License | | Why consider it |
|------|---------|---------|---|-----------------|
| **ruff** | 0.16.4 | MIT | 🟢 | Linter + formatter in one Rust binary; replaces flake8, isort, and black at ~100× speed. Single obvious pick. |
| **mypy** | 2.3.1 | MIT | 🟢 | The engine is ~94% annotated but nothing checks it, so `py.typed` was deliberately not shipped. Adopting mypy is the prerequisite for shipping that marker later. |
| **pyright** | 1.1.411 | MIT | 🟢 | Faster, stricter inference than mypy. Node-based binary. |
| **black** | 26.5.1 | MIT | 🟢 | If you want the canonical formatter rather than ruff's. |
| **flake8** | 7.3.0 | MIT | 🟢 | The classic; superseded by ruff for most projects. |
| **pre-commit** | 4.6.2 | MIT | 🟢 | Runs the above on commit. |
| 🔴 **pylint** | 4.0.7 | **GPL-2.0-or-later** | 🔴 | **Avoid** — the one major Python linter that is copyleft. `ruff` is MIT. See §15. |

> **Recommendation:** **ruff + mypy**, wired through **pre-commit**. All MIT. The marker file
> `py.typed` was removed rather than left as an unverified promise; adopting mypy is what would
> earn it back.

---

## 12. Task runner / CI

**Today:** GitHub Actions (`actions/checkout`, `actions/setup-python`, both MIT 🟢). Note the only
workflow was the MkDocs deploy and it has been removed — **there is currently no CI at all.**

| Tool | Version | License | | Why consider it |
|------|---------|---------|---|-----------------|
| **GitHub Actions** | — | MIT (the actions) | 🟢 | Already in use, free for public repos. |
| **nox** | 2026.8.17 | Apache-2.0 | 🟢 | Test matrix across the supported Python versions (3.12+), which nothing verifies today. Sessions are plain Python. |
| **tox** | 4.60.0 | MIT | 🟢 | The established equivalent; config-file driven. |
| **pre-commit** | 4.6.2 | MIT | 🟢 | Local gate before CI. |

> **Recommendation:** add a test workflow. `pyproject.toml` claims support for **five** Python
> versions (3.12 and 3.13) with nothing enforcing it — that is the largest verification gap in the repo.
> **nox** or a plain Actions matrix both close it.

---

## 13. Async and concurrency backends

**Today:** stdlib `asyncio`, `threading`, `concurrent.futures` — `AsyncioBridge`, `ThreadPool`,
`CancellationToken`, `CoroActionNode`.

| Library | Version | License | | Why consider it | Trade-off |
|---------|---------|---------|---|-----------------|-----------|
| **stdlib asyncio/threading** | — | PSF-2.0 | 🟢 | Current. Zero deps, universal. | Manual cancellation and lifetime handling. |
| **anyio** | 4.14.2 | MIT | 🟢 | One API over asyncio *and* trio — host apps on either backend could embed BTEng. Structured concurrency and cancel scopes map well onto `halt()`. | Runtime dependency in the async path. |
| **trio** | 0.34.0 | MIT OR Apache-2.0 | 🟢 | The strongest cancellation semantics in Python — a natural fit for tree halting. | Not asyncio; host apps are overwhelmingly asyncio. |
| **uvloop** | 0.22.1 | MIT AND Apache-2.0 | 🟢 | Drop-in faster event loop for tight tick rates. | CPython/Linux-macOS only. |

> **Recommendation:** stay on stdlib. **anyio** is the one worth revisiting if BTEng should embed in
> trio-based hosts as well as asyncio ones.

---

## 14. Behavior-tree and robotics ecosystem

Not dependencies — interop targets and prior art, included because their licenses determine how
freely BTEng can borrow, bridge, or claim compatibility.

| Project | Version | License | | Relevance |
|---------|---------|---------|---|-----------|
| **BehaviorTree.CPP** | 4.9.0 | MIT | 🟢 | BTEng's stated design inspiration. MIT means its **XML dialect and node semantics can be matched freely** — no attribution or copyleft constraint on compatibility. |
| **py_trees** | 2.5.0 | BSD-3-Clause | 🟢 | The other major Python BT library. Permissive — safe to study, benchmark against, or bridge. |
| **BehaviorTree.ROS2** | — | Apache-2.0 | 🟢 | Reference for a ROS 2 bridge (relevant to `bteng-ros2`). |
| **ROS 2 / rclpy** | — | Apache-2.0 | 🟢 | Permissive; no constraint on a BTEng ROS 2 integration. |
| **Nav2** | — | Apache-2.0 (mixed) | 🟢 | Behavior-tree-driven navigation stack; the natural integration target. |
| **nodeeditor** | — | BSD-3-Clause | 🟢 | Permissive Qt node editor — a viable base for an open Groot2 replacement. |
| 🔴 **Groot2** | — | **no license** | 🔴 | Not open source; no license file in the repo. See §15. |

---

## 15. License traps — what to avoid and why

Everything in this section is either **not open source** or carries obligations that conflict with
BTEng's MIT licensing or with "no limits". Several are the most obvious choice in their category,
which is exactly why they are listed.

| Item | License | Category | Why it fails |
|------|---------|----------|--------------|
| **pdoc3** | AGPL-3.0 | Docs | Strong network copyleft. **Easily confused with `pdoc` (MIT-0)** — different projects, near-identical names. Verify which one you install. |
| **pylint** | GPL-2.0-or-later | Linting | Copyleft dev tool. `ruff` (MIT) does the job. |
| **PlantUML** | LGPL-3.0 | Diagrams | Copyleft plus a Java runtime. `Mermaid` (MIT) or `D2` (MPL-2.0) instead. |
| **PyQt5 / PyQt6** | GPL-3.0 or paid commercial | GUI | Either GPL your GUI or buy a license. **PySide6** (LGPL-3.0) is the usable Qt binding; `Dear PyGui` (MIT) avoids the question entirely. |
| **urwid** | LGPL-2.1-only | TUI | Copyleft; `rich`/`textual` are MIT. |
| **Groot2** | none declared | BT monitor | No license file → default copyright, all rights reserved. Not open source, regardless of free availability. |
| **Material for MkDocs Insiders** | proprietary sponsorware | Docs theme | Paid-tier feature gating. The **public `mkdocs-material` package is MIT and unrestricted** — use that. |
| **Redis ≥ 7.4** | RSALv2 / SSPLv1 (8.0 adds AGPLv3) | Data store | **Not OSI-approved** in its RSAL/SSPL arms. The AGPLv3 option added in 8.0 is OSI-approved but strong network copyleft. Use **Valkey** (BSD-3-Clause) — the 7.2-lineage fork. Note the `redis-py` *client* is MIT and unaffected. |
| **docopt** | MIT | CLI | License is fine; **unmaintained since 2014**. Fails on maintenance risk, not licensing. |

### The relicensing pattern worth watching

Three of BTEng's own dependencies changed license mid-life (see the companion audit):
pyzmq LGPL→BSD, libzmq LGPL→MPL, setuptools PSF/ZPL→MIT. All three moved toward *more* permissive.
The industry trend since 2018 runs the other way — **Redis, Elastic, HashiCorp Terraform, MongoDB,
Sentry** all moved from open source to source-available under commercial pressure, and each produced
a community fork (Valkey, OpenSearch, OpenTofu).

The practical defence for a project like BTEng is not vigilance, it is **structure**:

1. **Prefer foundation- or community-governed projects** (Apache Software Foundation, Eclipse, PSF,
   PyPA) over single-vendor ones. Vendors relicense; foundations effectively cannot.
2. **Prefer permissive licenses that cannot be revoked** for code already released — a relicense
   only ever affects *future* versions, so a pinned permissive version stays permissive forever.
3. **Keep the dependency count at zero where possible.** BTEng's strongest protection against every
   risk in this document is that its engine imports nothing but the standard library.

---

## 16. What I would actually change

Ordered by value, and none of these break `dependencies = []`.

| # | Change | Area | Why | Effort |
|---|--------|------|-----|--------|
| ✅ | ~~`pyzmq>=24` → **`pyzmq>=26`**~~ | §1 | **Done.** Closed the LGPL window; 24.x/25.x were pre-relicense. | done |
| 2 | Add a **CI test workflow** (3.12, 3.13) | §12 | The supported Python versions are advertised and none are verified. There is currently no CI. | ~20 lines |
| 3 | Adopt **ruff + mypy** | §11 | The engine is ~94% annotated and unchecked; mypy reports 28 errors. Fixing them is what would justify shipping `py.typed`. | small |
| 4 | Diagrams → **Mermaid** | §3 | Removes the only undeclared system dependency and the only EPL-2.0 component; diagrams become diffable and live next to the prose. | medium |
| 5 | Declare **graphviz** in `[docs]` *or* delete `generate.py` | §3 | `docs/images/generate.py` imports a package nothing declares. Fix it or remove it — item 4 removes it. | 1 line |
| 6 | Add **pytest-cov**, **pytest-benchmark** to `[dev]` | §9 | Coverage is unmeasured; tick-rate regressions are the failure mode that matters most for this engine. | 2 lines |
| 7 | **rich** behind a `[cli]` extra | §8 | A live terminal tree view is the highest-value feature per dependency added. | medium |
| 8 | **websockets + Mermaid** dashboard | §4 | An open answer to Groot2 that reuses the event stream already built. | large |

---

## 17. Verification method

Every license, version, and release date in this document was checked against a primary source on
2026-08-23 — none are quoted from memory:

* **PyPI JSON API** (`https://pypi.org/pypi/<pkg>/json`) for 70+ Python packages, reading both the
  `license` / `license_expression` fields and the `License ::` trove classifiers.
* **GitHub REST API** (`/repos/{owner}/{repo}`) for non-PyPI tools, plus `/releases/latest` for
  current versions.
* **Upstream `LICENSE` files fetched directly** where an API returned `NOASSERTION` or `none` —
  this is how Redis's tri-license, D2's MPL-2.0, py_trees' BSD, zenoh's dual license, and
  **Groot2's absence of any license** were each confirmed rather than assumed.
* Anything that could not be positively confirmed is stated as unconfirmed rather than asserted.
