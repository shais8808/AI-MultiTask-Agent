/**
 * components/LogsPanel.jsx
 * ==========================
 * Why this file exists: implements the FRONTEND "Execution Logs"
 * requirement — a feed of past agent runs (prompt, tools, outcome,
 * duration) pulled from `/api/logs`, which surfaces
 * `logging/run_logger.py`'s persisted `ExecutionLog` rows.
 */
import React, { useEffect, useState, useCallback } from "react";
import {
  Box,
  List,
  ListItem,
  ListItemText,
  Typography,
  Chip,
  IconButton,
  Stack,
  CircularProgress,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import { listLogs } from "../api/client.js";

const OUTCOME_COLOR = {
  completed: "success",
  error: "error",
  awaiting_approval: "warning",
  in_progress: "info",
};

export default function LogsPanel({ refreshSignal }) {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listLogs(50);
      setLogs(data);
    } catch (e) {
      // Non-fatal
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshSignal]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ p: 1.5 }}>
        <Typography variant="subtitle2">Execution Logs</Typography>
        <IconButton size="small" onClick={load}>
          <RefreshIcon fontSize="small" />
        </IconButton>
      </Stack>
      <Box sx={{ flexGrow: 1, overflowY: "auto" }}>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}>
            <CircularProgress size={20} />
          </Box>
        ) : logs.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
            No runs logged yet.
          </Typography>
        ) : (
          <List dense disablePadding>
            {logs.map((log) => (
              <ListItem key={log.run_id} divider alignItems="flex-start">
                <ListItemText
                  primary={
                    <Typography variant="body2" noWrap title={log.prompt}>
                      {log.prompt}
                    </Typography>
                  }
                  secondary={
                    <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }} flexWrap="wrap">
                      <Chip
                        label={log.final_outcome}
                        size="small"
                        color={OUTCOME_COLOR[log.final_outcome] || "default"}
                      />
                      {log.duration_ms != null && (
                        <Chip label={`${log.duration_ms}ms`} size="small" variant="outlined" />
                      )}
                      <Chip label={log.approval_status} size="small" variant="outlined" />
                    </Stack>
                  }
                />
              </ListItem>
            ))}
          </List>
        )}
      </Box>
    </Box>
  );
}
