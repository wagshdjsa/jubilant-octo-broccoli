import { Button, Card, Empty, Form, Input, InputNumber, Select, Space, Tabs, Tag, Typography, message } from "antd";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import ReactFlow, { Background, Controls, MiniMap, addEdge, useEdgesState, useNodesState } from "reactflow";
import { api } from "../api";

const { Title } = Typography;
const { TextArea } = Input;

// DAG 节点类型映射（type 必须落在后端 DAGNode.type 的 Literal 里）
const NODE_TYPES = [
  { type: "retrieval", label: "检索" },
  { type: "llm", label: "LLM" },
  { type: "tool", label: "工具" },
  { type: "condition", label: "条件" },
  { type: "loop", label: "循环" },
  { type: "code", label: "代码" },
  { type: "subgraph", label: "子图" },
];

const LABEL_OF: Record<string, string> = Object.fromEntries(
  NODE_TYPES.map((t) => [t.type, t.label])
);

interface DagNode {
  id: string;
  type: string;
  params?: Record<string, any>;
}
interface DagEdge {
  from: string;
  to: string;
  when?: string | null;
}

export default function AgentEdit() {
  const { id } = useParams();
  const [agent, setAgent] = useState<any>(null);
  const [versions, setVersions] = useState<any[]>([]);
  const [form] = Form.useForm();

  // DAG 画布状态
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  const load = async () => {
    const [a, v] = await Promise.all([
      api.get(`/agents/${id}`),
      api.get(`/agents/${id}/versions`),
    ]);
    setAgent(a.data);
    setVersions(v.data);
    form.setFieldsValue({
      name: a.data.name,
      description: a.data.description,
      model: a.data.config?.model || "chat-default",
      prompt: a.data.config?.prompt || "",
      temperature: a.data.config?.temperature ?? 0.7,
    });
  };

  useEffect(() => { load(); }, [id]);

  // 从 config.dag 还原画布
  useEffect(() => {
    const dag = agent?.config?.dag;
    if (!dag) {
      setNodes([]);
      setEdges([]);
      return;
    }
    setNodes(
      (dag.nodes || []).map((n: DagNode) => ({
        id: n.id,
        type: "default",
        data: { label: `${LABEL_OF[n.type] || n.type} ${n.id}`, nodeType: n.type },
        // 位置存在 params._ui 里，避免保存后节点全叠在原点
        position: n.params?._ui || { x: 120, y: 120 },
      }))
    );
    setEdges(
      (dag.edges || []).map((e: DagEdge, i: number) => ({
        id: `e-${e.from}-${e.to}-${i}`,
        source: e.from,
        target: e.to,
        label: e.when || undefined,
      }))
    );
  }, [agent]);

  const save = async () => {
    const values = await form.validateFields();
    const { name, description, model, prompt, temperature } = values;
    await api.patch(`/agents/${id}`, {
      name,
      description,
      // 只覆盖这几个字段，其余（含 dag）沿用已有配置
      config: { ...(agent?.config || {}), model, prompt, temperature },
    });
    message.success("已保存草稿");
    load();
  };

  const saveDag = async () => {
    const dag = {
      nodes: nodes.map((n) => ({
        id: n.id,
        type: (n.data as any).nodeType || "llm",
        params: { _ui: n.position },
      })),
      edges: edges.map((e) => ({ from: e.source, to: e.target })),
    };
    try {
      await api.patch(`/agents/${id}`, {
        config: { ...(agent?.config || {}), dag },
      });
      message.success(`DAG 已保存（${dag.nodes.length} 节点 / ${dag.edges.length} 连线）`);
      load();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || "保存失败");
    }
  };

  const clearDag = async () => {
    setNodes([]);
    setEdges([]);
    await api.patch(`/agents/${id}`, {
      config: { ...(agent?.config || {}), dag: null },
    });
    message.success("已清空 DAG，运行时将回退到 ReAct 模板");
    load();
  };

  const publish = async () => {
    await api.post(`/agents/${id}/publish`);
    message.success("已发布");
    load();
  };

  const debug = async () => {
    const msg = prompt("输入消息：");
    if (!msg) return;
    try {
      const r = await api.post(`/agents/${id}/debug/chat`, {
        messages: [{ role: "user", content: msg }],
        stream: false,
      });
      const reply = r.data?.choices?.[0]?.message?.content ?? JSON.stringify(r.data, null, 2);
      message.info({ content: reply, duration: 8 });
    } catch (err: any) {
      message.error(err?.response?.data?.detail || "调试失败");
    }
  };

  const dagCount = agent?.config?.dag
    ? `${agent.config.dag.nodes?.length || 0} 节点 / ${agent.config.dag.edges?.length || 0} 连线`
    : "";

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card>
        <Title level={4} style={{ margin: 0 }}>
          {agent?.name || "加载中"}
          <Tag color={agent?.status === "published" ? "green" : "blue"} style={{ marginLeft: 12 }}>
            {agent?.status}
          </Tag>
          {agent?.latest_published_version && (
            <Tag style={{ marginLeft: 4 }}>已发布 v{agent.latest_published_version}</Tag>
          )}
        </Title>
      </Card>

      <Tabs
        items={[
          {
            key: "config",
            label: "基础配置",
            children: (
              <Card>
                <Form form={form} layout="vertical">
                  <Form.Item name="name" label="名称"><Input /></Form.Item>
                  <Form.Item name="description" label="描述"><Input /></Form.Item>
                  <Form.Item name="model" label="模型别名">
                    <Select options={[
                      { value: "chat-default", label: "chat-default" },
                      { value: "chat-fast", label: "chat-fast" },
                      { value: "chat-pro", label: "chat-pro" },
                    ]} />
                  </Form.Item>
                  <Form.Item name="prompt" label="系统提示词"><TextArea rows={6} /></Form.Item>
                  <Form.Item name="temperature" label="Temperature">
                    <InputNumber min={0} max={2} step={0.1} />
                  </Form.Item>
                  <Space>
                    <Button type="primary" onClick={save}>保存草稿</Button>
                    <Button onClick={debug}>Playground 调试</Button>
                    <Button type="primary" ghost onClick={publish}>发布</Button>
                  </Space>
                </Form>
              </Card>
            ),
          },
          {
            key: "dag",
            label: "DAG 可视化编排",
            children: (
              <Card>
                <Space style={{ marginBottom: 12 }} wrap>
                  {NODE_TYPES.map((t) => (
                    <Button
                      key={t.type}
                      onClick={() => {
                        // 必须用函数式更新：连续点击时 nodes 还是旧快照，
                        // 直接展开会互相覆盖，只剩最后一个节点。
                        setNodes((prev) => {
                          // 按类型内已用序号递增，避免删节点后再加导致 id 冲撞
                          // （后端 validate_dag 会校验 id 唯一）
                          const used = new Set(prev.map((n) => n.id));
                          let seq = 1;
                          while (used.has(`${t.type}-${seq}`)) seq++;
                          const total = prev.length;
                          return [
                            ...prev,
                            {
                              id: `${t.type}-${seq}`,
                              type: "default",
                              data: { label: `${t.label} ${seq}`, nodeType: t.type },
                              position: {
                                x: 120 + (total % 4) * 230,
                                y: 100 + Math.floor(total / 4) * 130,
                              },
                            },
                          ];
                        });
                      }}
                    >
                      + {t.label}
                    </Button>
                  ))}
                  <Button type="primary" onClick={saveDag}>保存 DAG</Button>
                  <Button danger onClick={clearDag}>清空</Button>
                  {dagCount && <Tag color="purple">已存: {dagCount}</Tag>}
                </Space>
                <div style={{ height: 500, border: "1px solid #d9d9d9", background: "#fafafa" }}>
                  {nodes.length === 0 && edges.length === 0 ? (
                    <Empty
                      style={{ paddingTop: 180 }}
                      description="暂无 DAG。上方按钮添加节点后连线，点「保存 DAG」生效；留空则运行时走 ReAct 模板"
                    />
                  ) : (
                    <ReactFlow
                      nodes={nodes}
                      edges={edges}
                      onNodesChange={onNodesChange}
                      onEdgesChange={onEdgesChange}
                      onConnect={(p) => setEdges((eds) => addEdge(p, eds))}
                      fitView
                    >
                      <Background />
                      <Controls />
                      <MiniMap />
                    </ReactFlow>
                  )}
                </div>
                <div style={{ marginTop: 12, color: "#888" }}>
                  说明: 画布产出的 DAG JSON 与简单配置等价；存在 dag 字段时，runtime 走 DAG 编译路径。
                </div>
              </Card>
            ),
          },
          {
            key: "versions",
            label: `版本 (${versions.length})`,
            children: (
              <Card>
                {versions.length === 0 ? (
                  <Empty description="暂无版本" />
                ) : (
                  versions.map((v) => (
                    <div key={v.id} style={{ padding: 8, borderBottom: "1px solid #eee" }}>
                      v{v.version} - <Tag color={v.status === "published" ? "green" : "blue"}>{v.status}</Tag>
                      {v.published_at && <span style={{ marginLeft: 8 }}>{v.published_at}</span>}
                    </div>
                  ))
                )}
              </Card>
            ),
          },
        ]}
      />
    </Space>
  );
}
