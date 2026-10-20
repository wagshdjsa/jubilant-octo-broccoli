import { Card, List, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { api } from "../api";

const { Title } = Typography;

// provider 是内部标识，展示时换成可读名
const PROVIDER_LABEL: Record<string, string> = {
  custom: "OpenAI 兼容",
  openai: "OpenAI",
  zhipu: "智谱",
  deepseek: "DeepSeek",
  anthropic: "Anthropic",
};

const PROVIDER_COLOR: Record<string, string> = {
  custom: "purple",
  openai: "green",
  zhipu: "blue",
  deepseek: "cyan",
  anthropic: "orange",
};

interface ModelAlias {
  alias: string;
  primary: { provider: string; model: string };
  fallbacks: any[];
  source?: string;
}

export default function ModelList() {
  const [models, setModels] = useState<ModelAlias[]>([]);

  useEffect(() => {
    api.get<ModelAlias[]>("/models").then((r) => setModels(r.data));
  }, []);

  return (
    <Card title={<Title level={4} style={{ margin: 0 }}>模型目录</Title>}>
      <List
        dataSource={models}
        renderItem={(m) => (
          <List.Item>
            <List.Item.Meta
              title={m.alias}
              description={
                <span>
                  <Tag color={PROVIDER_COLOR[m.primary.provider] || "default"}>
                    {PROVIDER_LABEL[m.primary.provider] || m.primary.provider}
                  </Tag>
                  {m.primary.model}
                  {m.fallbacks?.length > 0 && (
                    <span style={{ marginLeft: 8, color: "#888" }}>
                      + {m.fallbacks.length} fallback
                    </span>
                  )}
                  {m.source === "default" && (
                    <Tag style={{ marginLeft: 8 }}>内置默认</Tag>
                  )}
                </span>
              }
            />
          </List.Item>
        )}
      />
    </Card>
  );
}