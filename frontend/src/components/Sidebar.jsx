/**
 * components/Sidebar.jsx
 * ========================
 * Why this file exists: implements the FRONTEND "Sidebar" requirement —
 * lets the user switch which side panel (Tasks / Notes / Logs) is shown
 * next to the chat. Kept deliberately simple (no routing library) since
 * this is a single-page tool, not a multi-route app.
 */
import React from "react";
import { Box, List, ListItemButton, ListItemIcon, ListItemText, Typography, Divider } from "@mui/material";
import ChecklistIcon from "@mui/icons-material/Checklist";
import NotesIcon from "@mui/icons-material/Notes";
import HistoryIcon from "@mui/icons-material/History";
import SmartToyIcon from "@mui/icons-material/SmartToy";

const ITEMS = [
  { key: "tasks", label: "Tasks", icon: <ChecklistIcon fontSize="small" /> },
  { key: "notes", label: "Notes", icon: <NotesIcon fontSize="small" /> },
  { key: "logs", label: "Execution Logs", icon: <HistoryIcon fontSize="small" /> },
];

export default function Sidebar({ activePanel, onSelect }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, p: 2 }}>
        <SmartToyIcon color="primary" />
        <Typography variant="h6">Productivity Agent</Typography>
      </Box>
      <Divider />
      <List sx={{ flexGrow: 1 }}>
        {ITEMS.map((item) => (
          <ListItemButton
            key={item.key}
            selected={activePanel === item.key}
            onClick={() => onSelect(item.key)}
          >
            <ListItemIcon sx={{ minWidth: 36 }}>{item.icon}</ListItemIcon>
            <ListItemText primary={item.label} />
          </ListItemButton>
        ))}
      </List>
    </Box>
  );
}
