import { Layout, Menu } from "antd";
import { Outlet, useNavigate } from "react-router-dom";
import { RobotOutlined, ApartmentOutlined } from "@ant-design/icons";

const { Header, Sider, Content } = Layout;

export default function App() {
  const nav = useNavigate();

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ color: "#fff", fontSize: 18, fontWeight: 600 }}>
        智能体中台 · Agent Platform
      </Header>
      <Layout>
        <Sider width={200}>
          <Menu
            mode="inline"
            defaultSelectedKeys={["agents"]}
            onClick={({ key }) => {
              if (key === "agents") nav("/");
              if (key === "models") nav("/models");
            }}
            items={[
              { key: "agents", icon: <RobotOutlined />, label: "智能体" },
              { key: "models", icon: <ApartmentOutlined />, label: "模型目录" },
            ]}
          />
        </Sider>
        <Content style={{ padding: 24, background: "#f5f5f5" }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}