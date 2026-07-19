/**
 * api/client.js
 * ==============
 *
 * Why this file exists
 * ---------------------
 * Single place for every HTTP call the frontend makes to the FastAPI
 * backend. Components never call axios directly — they call these
 * functions — so the base URL, error handling, and endpoint shapes are
 * defined exactly once and stay in sync with `backend/app/routes/*.py`.
 */
import axios from "axios";

// In local dev this is empty, so requests go to relative "/api" paths,
// which Vite's dev server proxies to the backend (see vite.config.js).
// In production (e.g. Render's static site), there is no proxy, so
// VITE_API_BASE_URL must be set to the deployed backend's origin,
// e.g. "https://productivity-agent-backend.onrender.com".
const API_ORIGIN = import.meta.env.VITE_API_BASE_URL || "";

const client = axios.create({
  baseURL: `${API_ORIGIN}/api`,
  timeout: 35000, // slightly above the backend's REQUEST_TIMEOUT_SECONDS=30
});

export const sendChatMessage = (sessionId, message, llmProvider = null, model = null) =>
  client
    .post("/chat", {
      session_id: sessionId,
      message,
      llm_provider: llmProvider,
      model,
    })
    .then((r) => r.data);

export const listTasks = (params = {}) =>
  client.get("/tasks", { params }).then((r) => r.data);

export const createTask = (task) => client.post("/tasks", task).then((r) => r.data);

export const updateTask = (taskId, updates) =>
  client.put(`/tasks/${taskId}`, { task_id: taskId, ...updates }).then((r) => r.data);

export const completeTask = (taskId) =>
  client.post(`/tasks/${taskId}/complete`).then((r) => r.data);

export const deleteTask = (taskId) => client.delete(`/tasks/${taskId}`).then((r) => r.data);

export const listNotes = (limit = 100) =>
  client.get("/notes", { params: { limit } }).then((r) => r.data);

export const searchNotes = (query) =>
  client.get("/notes/search", { params: { query } }).then((r) => r.data);

export const createNote = (note) => client.post("/notes", note).then((r) => r.data);

export const listPendingApprovals = () =>
  client.get("/approvals").then((r) => r.data.pending);

export const submitApprovalDecision = (approvalId, decision, editedArguments) =>
  client
    .post(`/approvals/${approvalId}`, {
      approval_id: approvalId,
      decision,
      edited_arguments: editedArguments ?? null,
    })
    .then((r) => r.data);

export const listLogs = (limit = 50) =>
  client.get("/logs", { params: { limit } }).then((r) => r.data.logs);

export const checkHealth = () => client.get("/health", { baseURL: API_ORIGIN || "/" }).then((r) => r.data);

export default client;
