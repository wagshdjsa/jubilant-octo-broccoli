"""graph_builder 单测: ReAct 模板编译 + 简单 DAG 编译"""
from packages.common.schemas import AgentConfig, DAGConfig, DAGNode, DAGEdge


def test_react_compile():
    config = AgentConfig(
        model="chat-default",
        prompt="You are a test agent.",
        tools=["calculator"],
        knowledge_bases=[],
    )
    from services.agent_runtime.graph_builder import build_graph
    graph = build_graph("agent-1", 1, config)
    assert graph is not None


def test_dag_validate():
    dag = DAGConfig(
        nodes=[
            DAGNode(id="n1", type="retrieval", params={"kb_id": "x"}),
            DAGNode(id="n2", type="llm", params={"alias": "chat-default"}),
        ],
        edges=[DAGEdge(**{"from": "n1", "to": "n2"})],
    )
    config = AgentConfig(model="chat-default", prompt="x", dag=dag)
    config.dag.validate_dag()


def test_dag_duplicate_node_id_fails():
    try:
        dag = DAGConfig(
            nodes=[
                DAGNode(id="n1", type="llm", params={}),
                DAGNode(id="n1", type="tool", params={}),
            ],
            edges=[],
        )
        dag.validate_dag()
        assert False, "should raise"
    except ValueError:
        pass


def test_dag_compile():
    dag = DAGConfig(
        nodes=[DAGNode(id="n1", type="llm", params={"alias": "chat-default"})],
        edges=[],
    )
    config = AgentConfig(model="chat-default", prompt="x", dag=dag)
    from services.agent_runtime.graph_builder import build_graph
    graph = build_graph("agent-2", 1, config)
    assert graph is not None


if __name__ == "__main__":
    test_react_compile()
    test_dag_validate()
    test_dag_duplicate_node_id_fails()
    test_dag_compile()
    print("OK: graph_builder tests passed")