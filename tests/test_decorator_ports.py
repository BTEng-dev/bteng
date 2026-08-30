"""Control and decorator parameters are ports, re-read every tick.

Before this, `<Retry num_attempts="{max_tries}"/>` was rejected outright: the
parser turned those attributes into constructor arguments while the tree was
being built, when no blackboard existed yet. So a retry budget, a timeout or a
rate was frozen at build time, while BehaviorTree.CPP has allowed exactly that
remapping for years.

Resolution order, for every parameter here: blackboard mapping, then a literal
XML attribute, then the constructor argument.
"""
from __future__ import annotations

from bteng.blackboard.blackboard import Blackboard
from bteng.core.node import NodeConfig, NodeStatus
from bteng.core.tree import Tree, TreeMetadata
from bteng.factory.factory import NodeFactory
from bteng.nodes.control.parallel import ParallelNode
from bteng.nodes.decorators.rate_controller import RateController
from bteng.nodes.decorators.retry import Retry
from bteng.nodes.decorators.timeout import Timeout
from bteng.nodes.leaf.action import ActionNode
from bteng.testing.mock_nodes import MockActionNode
from bteng.xml_parser.parser import XMLTreeParser


class _CountingFail(ActionNode):
    def __init__(self, name="fail"):
        super().__init__(name)
        self.ticks = 0

    def tick(self):
        self.ticks += 1
        return NodeStatus.FAILURE


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t


def _bound(node_cls, port, key, bb, **kw):
    """Build *node_cls* with *port* remapped onto blackboard *key*."""
    return node_cls(config=NodeConfig(blackboard=bb, input_ports={port: key}), **kw)


# ── Retry ───────────────────────────────────────────────────────────────────────

def test_retry_budget_comes_from_the_blackboard():
    bb = Blackboard(scope_name="retry_bb")
    bb.set("tries", 2)
    child = _CountingFail()
    node = _bound(Retry, "num_attempts", "tries", bb, child=child, max_attempts=99)

    while node.execute_tick() == NodeStatus.RUNNING:
        pass
    assert child.ticks == 2, "the constructor's 99 should not have been used"


def test_retry_budget_can_change_between_activations():
    bb = Blackboard(scope_name="retry_bb2")
    bb.set("tries", 1)
    child = _CountingFail()
    node = _bound(Retry, "num_attempts", "tries", bb, child=child)

    assert node.execute_tick() == NodeStatus.FAILURE
    assert child.ticks == 1

    bb.set("tries", 3)
    while node.execute_tick() == NodeStatus.RUNNING:
        pass
    assert child.ticks == 4, "second activation should have spent a budget of 3"


def test_retry_falls_back_to_the_constructor_without_a_mapping():
    child = _CountingFail()
    node = Retry(child=child, max_attempts=2)
    while node.execute_tick() == NodeStatus.RUNNING:
        pass
    assert child.ticks == 2


def test_retry_survives_a_non_numeric_blackboard_value():
    bb = Blackboard(scope_name="retry_bad")
    bb.set("tries", "not a number")
    child = _CountingFail()
    node = _bound(Retry, "num_attempts", "tries", bb, child=child, max_attempts=1)
    assert node.execute_tick() == NodeStatus.FAILURE
    assert child.ticks == 1
    assert "not an integer" in node.feedback_message


# ── Timeout ─────────────────────────────────────────────────────────────────────

def test_timeout_duration_comes_from_the_blackboard():
    bb = Blackboard(scope_name="timeout_bb")
    bb.set("budget", 5.0)
    clock = _FakeClock()
    running = MockActionNode("slow")
    running.set_status(NodeStatus.RUNNING)
    node = _bound(Timeout, "duration", "budget", bb,
                  child=running, duration=0.001, clock=clock)

    assert node.execute_tick() == NodeStatus.RUNNING
    clock.t = 3.0
    assert node.execute_tick() == NodeStatus.RUNNING, "3s < the blackboard's 5s"
    clock.t = 6.0
    assert node.execute_tick() == NodeStatus.FAILURE


def test_timeout_falls_back_to_the_constructor():
    clock = _FakeClock()
    running = MockActionNode("slow")
    running.set_status(NodeStatus.RUNNING)
    node = Timeout(child=running, duration=2.0, clock=clock)
    node.execute_tick()
    clock.t = 3.0
    assert node.execute_tick() == NodeStatus.FAILURE


# ── RateController ──────────────────────────────────────────────────────────────

def test_rate_comes_from_the_blackboard():
    bb = Blackboard(scope_name="rate_bb")
    bb.set("rate", 2.0)  # one tick every 0.5s
    clock = _FakeClock()
    child = MockActionNode("child")
    node = _bound(RateController, "hz", "rate", bb, child=child, hz=1000.0, clock=clock)

    node.execute_tick()
    first = child.tick_count_local
    clock.t = 0.1
    node.execute_tick()
    assert child.tick_count_local == first, "0.1s < the 0.5s period from the blackboard"
    clock.t = 0.6
    node.execute_tick()
    assert child.tick_count_local == first + 1


def test_a_non_positive_rate_keeps_the_previous_period():
    bb = Blackboard(scope_name="rate_bad")
    bb.set("rate", 0)
    clock = _FakeClock()
    node = _bound(RateController, "hz", "rate", bb,
                  child=MockActionNode("c"), hz=1.0, clock=clock)
    node.execute_tick()  # must not raise ZeroDivisionError
    assert "not positive" in node.feedback_message


# ── Parallel ────────────────────────────────────────────────────────────────────

def test_parallel_thresholds_come_from_the_blackboard():
    bb = Blackboard(scope_name="par_bb")
    bb.set("wanted", 1)
    good, bad = MockActionNode("good"), MockActionNode("bad")
    bad.set_status(NodeStatus.FAILURE)
    node = ParallelNode(
        "par", children=[good, bad],
        config=NodeConfig(blackboard=bb, input_ports={"success_threshold": "wanted"}),
        failure_threshold=2,
    )
    assert node.execute_tick() == NodeStatus.SUCCESS


def test_parallel_failure_threshold_is_a_port_too():
    bb = Blackboard(scope_name="par_bb2")
    bb.set("tolerated", 2)
    a, b = MockActionNode("a"), MockActionNode("b")
    a.set_status(NodeStatus.FAILURE)
    b.set_status(NodeStatus.FAILURE)
    node = ParallelNode(
        "par", children=[a, b],
        config=NodeConfig(blackboard=bb, input_ports={"failure_threshold": "tolerated"}),
    )
    assert node.execute_tick() == NodeStatus.FAILURE


# ── End to end through XML ──────────────────────────────────────────────────────

_XML = """
<BTEng>
  <Tree ID="main">
    <Retry name="r" num_attempts="{tries}">
      <MockAction name="child"/>
    </Retry>
  </Tree>
</BTEng>
"""


def test_xml_ref_reaches_the_node_and_validates():
    factory = NodeFactory()
    factory.register(MockActionNode, "MockAction")
    bb = Blackboard(scope_name="xml_bb")
    bb.set("tries", 2)

    root = XMLTreeParser(factory=factory).parse_string(_XML, blackboard=bb)
    Tree(TreeMetadata(id="t"), root).validate()

    assert root.config.input_ports == {"num_attempts": "tries"}
    assert root._budget() == 2


_PARALLEL_XML = """
<BTEng>
  <Tree ID="main">
    <Parallel name="par" success_threshold="{wanted}" failure_threshold="{tolerated}">
      <MockAction name="a"/>
      <MockAction name="b"/>
    </Parallel>
  </Tree>
</BTEng>
"""


def test_xml_parallel_thresholds_bind_and_validate():
    """Both thresholds must be *declared* ports, or Tree.validate() rejects the tree.

    The parser binds any {ref} attribute as an input port, and validate_node()
    rejects a mapping to an undeclared port -- so a failure_threshold that only
    _failure_from_port() knew about parsed and ticked correctly, then failed
    validation the moment the tree was wrapped in a Tree.
    """
    factory = NodeFactory()
    factory.register(MockActionNode, "MockAction")
    bb = Blackboard(scope_name="xml_par_bb")
    bb.set("wanted", 2)
    bb.set("tolerated", 1)

    root = XMLTreeParser(factory=factory).parse_string(_PARALLEL_XML, blackboard=bb)
    Tree(TreeMetadata(id="t"), root).validate()

    assert root.config.input_ports == {
        "success_threshold": "wanted",
        "failure_threshold": "tolerated",
    }
    assert root._effective_thresholds() == (2, 1)
