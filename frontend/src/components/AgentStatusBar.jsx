/**
 * components/AgentStatusBar.jsx
 * ================================
 * Why this file exists: implements the FRONTEND "Agent Status" and part
 * of "Tool Status" requirements — a persistent top bar showing whether
 * the backend/LLM is reachable and configured, plus the live status of
 * the most recent agent run (idle / thinking / awaiting approval / error).
 */
import React, { useEffect, useState } from "react";
import { AppBar, Toolbar, Typography, Chip, Box } from "@mui/material";
import CircleIcon from "@mui/icons-material/Circle";
import { checkHealth } from "../api/client.js";

const STATUS_LABEL = {
  idle: "Idle",
  completed: "Completed",
  awaiting_approval: "Awaiting Approval",
  error: "Error",
  in_progress: "Working...",
};
const STATUS_COLOR = {
  idle: "default",
  completed: "success",
  awaiting_approval: "warning",
  error: "error",
  in_progress: "info",
};

export default function AgentStatusBar({ agentStatus }) {
  const [health, setHealth] = useState(null);

  useEffect(() => {
    checkHealth()
      .then(setHealth)
      .catch(() => setHealth({ status: "unreachable", llm_configured: false }));
  }, []);

  const backendOk = health?.status === "ok";

  return (
    <AppBar position="static" color="transparent" elevation={0} sx={{ borderBottom: "1px solid", borderColor: "divider" }}>
      <Toolbar variant="dense" sx={{ gap: 2 }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <CircleIcon sx={{ fontSize: 10 }} color={backendOk ? "success" : "error"} />
          <Typography variant="caption" color="text.secondary">
            Backend {backendOk ? "connected" : "unreachable"}
          </Typography>
        </Box>
        {health?.model && (
          <Typography variant="caption" color="text.secondary">
            Model: {health.model}
          </Typography>
        )}
        <Box sx={{ flexGrow: 1 }} />
        <Chip
          size="small"
          label={STATUS_LABEL[agentStatus] || "Idle"}
          color={STATUS_COLOR[agentStatus] || "default"}
          variant={agentStatus ? "filled" : "outlined"}
        />
      </Toolbar>
    </AppBar>
  );
}
