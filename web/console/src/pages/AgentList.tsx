import { Button, Card, List, Space, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";

const { Title } = Typography;

interface Agent {
  id: string;
  name: string;
  description?: string;
  status: string;
  latest_published_version?: number | null;
}

export default function AgentList() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const nav = useNavigate();

  const load = async () => {
    const r = await api.get<Agent[]>("/agents");
    setAgents(r.data);
  };

  useEffect(() => { load(); }, []);

  const create = async () => {
    const name = prompt("智能体名称？");
    if (!name) return;
    const r = await api.post<Agent>("/agents", {
      name,
      config: {
        model: "chat-default",
        prompt: "You are a helpful assistant.",
        tools: [],
        knowledge_bases: [],
      },
    });
    nav(`/agents/${r.data.id}`);
  };

  return (
    <Card
      title={<Title level={4} style={{ margin: 0 }}>智能体列表</Title>}
      extra={<Button type="primary" onClick={create}>新建智能体</Button>}
    >
      <List
        dataSource={agents}
        renderItem={(a) => (
          <List.Item
            actions={[
              <Link key="edit" to={`/agents/${a.id}`}>编辑</Link>,
            ]}
          >
            <List.Item.Meta
              title={a.name}
              description={
                <Space>
                  <Tag color={a.status === "published" ? "green" : "blue"}>{a.status}</Tag>
                  {a.latest_published_version && <Tag>v{a.latest_published_version}</Tag>}
                  {a.description}
                </Space>
              }
            />
          </List.Item>
        )}
      />
    </Card>
  );
}