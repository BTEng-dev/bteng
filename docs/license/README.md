# License documentation

BTEng is **MIT** — see [LICENSE](../../LICENSE).

The third-party audit lives **inside the package** at [`bteng/licenses/`](../../bteng/licenses/),
so it ships with every install rather than only existing on GitHub. Anyone who runs
`pip install bteng` can read it without leaving their machine:

```bash
bteng licenses          # the third-party audit
bteng licenses --path   # where it lives on disk
```

Only the audit ships inside the package — it answers "what am I installing, and under
what licence". The alternatives survey is a planning document and stays here in `docs/`.

| Document | Contents |
|----------|----------|
| [Third-party license audit](../../bteng/licenses/THIRD_PARTY_LICENSES.md) | Every library used to build, test, document, and optionally run BTEng: current version, current license, and whether that license ever changed (and at which version). Includes the compatibility verdict per distribution scenario. |
| [Open-source alternatives](OPEN_SOURCE_ALTERNATIVES.md) | Candidate libraries per functional area, restricted to open-source options with no usage limits. Includes the license traps to avoid and a prioritised change list. |

Both carry the **scan date** they were verified on. Licenses change upstream; re-scan before
relying on either for a legal review.

## The short version

BTEng's engine has **zero runtime dependencies** — `bteng/` imports only the standard library, and
the single non-stdlib import (`zmq`) is lazily guarded behind the optional `[zmq]` extra. The built
wheel contains no third-party code at all: every file in it is BTEng's own.

A downstream user — including a closed-source commercial one — can depend on BTEng and owes nothing
beyond retaining the MIT notice.
