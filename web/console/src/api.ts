import axios from "axios";

// 管理 Token 写死在浏览器即可（M2 替换为登录）
export const ADMIN_TOKEN =
  (import.meta as any).env?.VITE_ADMIN_TOKEN ||
  (window as any).ADMIN_TOKEN ||
  "ChangeMe!2026";
// 注意: 需与项目根 .env 的 MANAGER_ADMIN_TOKEN 一致，否则全部管理接口 401

export const api = axios.create({
  baseURL: "/api/v1",
  headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
});

// 运行时接口走 OpenAI 兼容协议
export const runtimeApi = axios.create({
  baseURL: "/v1",
});