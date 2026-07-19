/**
 * components/TaskPanel.jsx
 * ==========================
 * Why this file exists: implements the FRONTEND "Task Panel" requirement
 * — a live view of tasks with status/priority indicators and quick
 * complete/delete actions, backed directly by `/api/tasks` (not the agent
 * chat path) for immediate, no-LLM-roundtrip management.
 */
import React, { useEffect, useState, useCallback } from "react";
import {
  Box,
  List,
  ListItem,
  ListItemText,
  Chip,
  IconButton,
  Typography,
  Stack,
  ToggleButtonGroup,
  ToggleButton,
  CircularProgress,
  Tooltip,
} from "@mui/material";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import RefreshIcon from "@mui/icons-material/Refresh";
import { listTasks, completeTask, deleteTask } from "../api/client.js";

const PRIORITY_COLOR = { low: "default", medium: "info", high: "warning", critical: "error" };
const STATUS_COLOR = {
  pending: "default",
  in_progress: "info",
  blocked: "warning",
  completed: "success",
  cancelled: "default",
};

export default function TaskPanel({ refreshSignal }) {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("active");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = filter === "overdue" ? { overdue_only: true } : {};
      const data = await listTasks(params);
      let items = data.tasks;
      if (filter === "active") {
        items = items.filter((t) => !["completed", "cancelled"].includes(t.status));
      } else if (filter === "completed") {
        items = items.filter((t) => t.status === "completed");
      }
      setTasks(items);
    } catch (e) {
      // Non-fatal: leave prior list visible, panel simply doesn't refresh.
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    load();
  }, [load, refreshSignal]);

  const handleComplete = async (taskId) => {
    await completeTask(taskId);
    load();
  };

  const handleDelete = async (taskId) => {
    await deleteTask(taskId);
    load();
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ p: 1.5 }}>
        <Typography variant="subtitle2">Tasks</Typography>
        <Stack direction="row" spacing={1} alignItems="center">
          <ToggleButtonGroup
            size="small"
            exclusive
            value={filter}
            onChange={(_, v) => v && setFilter(v)}
          >
            <ToggleButton value="active">Active</ToggleButton>
            <ToggleButton value="overdue">Overdue</ToggleButton>
            <ToggleButton value="completed">Done</ToggleButton>
          </ToggleButtonGroup>
          <IconButton size="small" onClick={load}>
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Stack>
      </Stack>
      <Box sx={{ flexGrow: 1, overflowY: "auto" }}>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}>
            <CircularProgress size={20} />
          </Box>
        ) : tasks.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
            No tasks here.
          </Typography>
        ) : (
          <List dense disablePadding>
            {tasks.map((t) => (
              <ListItem
                key={t.id}
                divider
                secondaryAction={
                  <Stack direction="row" spacing={0.5}>
                    {t.status !== "completed" && (
                      <Tooltip title="Mark complete">
                        <IconButton size="small" onClick={() => handleComplete(t.id)}>
                          <CheckCircleOutlineIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                    <Tooltip title="Delete">
                      <IconButton size="small" onClick={() => handleDelete(t.id)}>
                        <DeleteOutlineIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                }
              >
                <ListItemText
                  primary={t.title}
                  secondary={
                    <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }}>
                      <Chip label={t.priority} size="small" color={PRIORITY_COLOR[t.priority]} />
                      <Chip label={t.status} size="small" color={STATUS_COLOR[t.status]} variant="outlined" />
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
