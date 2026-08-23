# Install

BTEng requires **Python 3.12 or newer**.

> [!NOTE]
> If your system Python is older — Ubuntu 22.04 and ROS 2 Humble ship 3.10 — install a
> newer interpreter alongside it and create the virtual environment from that:
>
> ```bash
> sudo add-apt-repository ppa:deadsnakes/ppa   # Debian/Ubuntu
> sudo apt install python3.12 python3.12-venv
> python3.12 -m venv .venv && source .venv/bin/activate
> pip install bteng
> ```
>
> The system interpreter is left untouched.

---

## Standard install

```bash
pip install bteng
```

Verify:

```bash
python3 -c "import bteng; print(bteng.__version__)"
```

---

## Virtual environment (recommended)

Always use a virtual environment to avoid polluting the system Python:

```bash
python3 -m venv .venv
source .venv/bin/activate    # Linux / macOS
.venv\Scripts\activate       # Windows

pip install bteng
```

---

## Optional extras

### ZMQ event streaming

For streaming live execution events to an external dashboard or monitoring process:

```bash
pip install "bteng[zmq]"
```

Without this extra, `ZmqPublisher` raises `ImportError` on import. All other BTEng
features work without ZMQ.

---

## Development install (from source)

If you are contributing to BTEng or want to run the test suite:

```bash
git clone https://github.com/BTEng-dev/bteng.git
cd bteng
python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
pytest -q
```

The `-e` flag installs the package in editable mode — changes to the source are
reflected immediately without reinstalling.

### Running tests

```bash
# Full test suite
pytest tests/ -v

# Individual suites
pytest tests/test_bugs.py           # 70 regression tests
pytest tests/test_validation.py     # 21 port validation tests
pytest tests/test_reactive.py       # 25 reactive execution tests
```

All tests should pass. If any fail, open an issue on GitHub.

---

## Verifying the install

After installing, confirm the package version and import:

```python
import bteng

print(bteng.__version__)

# Quick smoke test
from bteng import Blackboard, NodeStatus, TreeBuilder, TreeExecutor

bb = Blackboard.create("test")
bb.set("ok", True)

tree = (
    TreeBuilder(blackboard=bb)
    .sequence("root")
        .condition("OK", lambda: bb.get("ok", False))
        .action("Pass", lambda: NodeStatus.SUCCESS)
    .end()
    .build()
)

executor = TreeExecutor()
executor.set_tree(tree)
result = executor.tick_until_result(max_ticks=5)
print(result)   # NodeStatus.SUCCESS
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError: No module named 'bteng'` | Activate the virtual environment: `source .venv/bin/activate` |
| `ImportError` when importing `ZmqPublisher` | Install the ZMQ extra: `pip install "bteng[zmq]"` |
| `TreeValidationError` on the smoke test | Check that `NodeConfig` port mappings are correct; see [Ports and validation](../advanced/ports-validation.md) |
| Old version after `pip install` | Run `pip install --upgrade bteng` |

Next: [5-minute Quick Start](quickstart.md).
