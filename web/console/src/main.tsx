import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { ConfigProvider } from "antd";
import "antd/dist/reset.css";
import "reactflow/dist/style.css";
import App from "./App";
import AgentList from "./pages/AgentList";
import AgentEdit from "./pages/AgentEdit";
import ModelList from "./pages/ModelList";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<App />}>
            <Route index element={<AgentList />} />
            <Route path="agents/:id" element={<AgentEdit />} />
            <Route path="models" element={<ModelList />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>
);