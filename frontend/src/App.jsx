/**
 * App.jsx
 * ========
 * Why this file exists: top-level layout composing every component into
 * the full FRONTEND spec — Chat, Sidebar, Task/Notes/Logs panels,
 * Approval Modal, Agent Status, and a responsive two/three-column layout
 * that collapses to a single column on small screens.
 */
import React, { useMemo, useState } from "react";
import { Box, Grid, Paper, useMediaQuery } from "@mui/material";
import Sidebar from "./components/Sidebar.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import TaskPanel from "./components/TaskPanel.jsx";
import NotesPanel from "./components/NotesPanel.jsx";
import LogsPanel from "./components/LogsPanel.jsx";
import ApprovalModal from "./components/ApprovalModal.jsx";
import AgentStatusBar from "./components/AgentStatusBar.jsx";

function getOrCreateSessionId() {
  // In-memory only (no localStorage per the artifact/browser-storage
  // constraints of this environment) — a fresh session per page load is
  // an acceptable tradeoff for this project's scope.
  return `session-${Math.random().toString(36).slice(2)}-${Date.now()}`;
}

export default function App() {
  const [sessionId] = useState(getOrCreateSessionId);
  const [activePanel, setActivePanel] = useState("tasks");
  const [pendingApproval, setPendingApproval] = useState(null);
  const [agentStatus, setAgentStatus] = useState("idle");
  const [agentPhase, setAgentPhase] = useState(null);
  const [refreshSignal, setRefreshSignal] = useState(0);

  const isSmallScreen = useMediaQuery("(max-width:900px)");

  const handleAgentResult = (result) => {
    setAgentStatus(result.status);
    if (result.status === "awaiting_approval" && result.pending_approval) {
      setPendingApproval(result.pending_approval);
    }
    // Tasks/notes may have changed as a side effect of the tool call —
    // bump the refresh signal so the side panels re-fetch.
    setRefreshSignal((n) => n + 1);
  };

  const handleApprovalResolved = (result) => {
    setPendingApproval(null);
    setAgentStatus(result.status);
    setRefreshSignal((n) => n + 1);
  };

  const PanelComponent = useMemo(() => {
    if (activePanel === "tasks") return <TaskPanel refreshSignal={refreshSignal} />;
    if (activePanel === "notes") return <NotesPanel refreshSignal={refreshSignal} />;
    return <LogsPanel refreshSignal={refreshSignal} />;
  }, [activePanel, refreshSignal]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <AgentStatusBar agentStatus={agentStatus} phase={agentPhase} />
      <Box sx={{ flexGrow: 1, overflow: "hidden", p: 1.5 }}>
        <Grid container spacing={1.5} sx={{ height: "100%" }}>
          {!isSmallScreen && (
            <Grid item xs={12} md={2.2} sx={{ height: "100%" }}>
              <Paper variant="outlined" sx={{ height: "100%", overflow: "hidden" }}>
                <Sidebar activePanel={activePanel} onSelect={setActivePanel} />
              </Paper>
            </Grid>
          )}
          <Grid item xs={12} md={isSmallScreen ? 12 : 6.4} sx={{ height: isSmallScreen ? "55%" : "100%" }}>
            <Paper variant="outlined" sx={{ height: "100%", overflow: "hidden" }}>
              <ChatPanel
                sessionId={sessionId}
                onAgentResult={handleAgentResult}
                onPhaseChange={setAgentPhase}
                pendingApproval={pendingApproval}
              />
            </Paper>
          </Grid>
          <Grid item xs={12} md={isSmallScreen ? 12 : 3.4} sx={{ height: isSmallScreen ? "45%" : "100%" }}>
            <Paper variant="outlined" sx={{ height: "100%", overflow: "hidden" }}>
              {isSmallScreen && <Sidebar activePanel={activePanel} onSelect={setActivePanel} />}
              {PanelComponent}
            </Paper>
          </Grid>
        </Grid>
      </Box>
      <ApprovalModal
        approval={pendingApproval}
        onResolved={handleApprovalResolved}
        onClose={() => {}}
      />
    </Box>
  );
}
