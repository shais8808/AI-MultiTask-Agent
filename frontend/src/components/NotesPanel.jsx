/**
 * components/NotesPanel.jsx
 * ===========================
 * Why this file exists: implements the FRONTEND "Notes Panel" requirement
 * — browse and search saved notes directly via `/api/notes`.
 */
import React, { useEffect, useState, useCallback } from "react";
import {
  Box,
  List,
  ListItem,
  ListItemText,
  Typography,
  TextField,
  InputAdornment,
  CircularProgress,
  Chip,
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import { listNotes, searchNotes } from "../api/client.js";

export default function NotesPanel({ refreshSignal }) {
  const [notes, setNotes] = useState([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = query.trim() ? await searchNotes(query.trim()) : { notes: await listNotes() };
      setNotes(data.notes || data);
    } catch (e) {
      // Non-fatal
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    const timeout = setTimeout(load, 250); // debounce search input
    return () => clearTimeout(timeout);
  }, [load, refreshSignal]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Box sx={{ p: 1.5 }}>
        <TextField
          fullWidth
          size="small"
          placeholder="Search notes..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" />
              </InputAdornment>
            ),
          }}
        />
      </Box>
      <Box sx={{ flexGrow: 1, overflowY: "auto" }}>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}>
            <CircularProgress size={20} />
          </Box>
        ) : notes.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
            No notes found.
          </Typography>
        ) : (
          <List dense disablePadding>
            {notes.map((n) => (
              <ListItem key={n.id} divider alignItems="flex-start">
                <ListItemText
                  primary={n.title}
                  secondary={
                    <>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{
                          display: "-webkit-box",
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: "vertical",
                          overflow: "hidden",
                        }}
                      >
                        {n.content}
                      </Typography>
                      <Box sx={{ mt: 0.5 }}>
                        <Chip label={n.category} size="small" variant="outlined" />
                      </Box>
                    </>
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
