/**
 * components/ApprovalModal.jsx
 * ==============================
 * Why this file exists: implements the frontend half of the APPROVAL
 * RULES requirement — displaying Action, Tool, Arguments, and
 * Approve/Reject/Edit controls whenever the agent selects a sensitive
 * tool. Calls `submitApprovalDecision` and reports the result back up to
 * `App.jsx` so the chat thread and task list can refresh.
 */
import React, { useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  Box,
  Chip,
  TextField,
  Alert,
  Stack,
} from "@mui/material";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { submitApprovalDecision } from "../api/client.js";

export default function ApprovalModal({ approval, onResolved, onClose }) {
  const [editing, setEditing] = useState(false);
  const [editedArgsText, setEditedArgsText] = useState(
    approval ? JSON.stringify(approval.tool_call.arguments, null, 2) : "{}"
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  if (!approval) return null;

  const handleDecision = async (decision) => {
    setSubmitting(true);
    setError(null);
    try {
      let editedArguments;
      if (decision === "edited") {
        try {
          editedArguments = JSON.parse(editedArgsText);
        } catch (e) {
          setError("Edited arguments must be valid JSON.");
          setSubmitting(false);
          return;
        }
      }
      const result = await submitApprovalDecision(approval.approval_id, decision, editedArguments);
      onResolved(result);
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to submit decision. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={Boolean(approval)} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <WarningAmberIcon color="warning" />
        Approval Required
      </DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          <Typography variant="body1">{approval.action_summary}</Typography>

          <Box>
            <Typography variant="caption" color="text.secondary">
              Tool
            </Typography>
            <Box>
              <Chip label={approval.tool_call.tool_name} size="small" color="primary" variant="outlined" />
            </Box>
          </Box>

          <Box>
            <Typography variant="caption" color="text.secondary">
              Arguments
            </Typography>
            {editing ? (
              <TextField
                multiline
                fullWidth
                minRows={4}
                value={editedArgsText}
                onChange={(e) => setEditedArgsText(e.target.value)}
                sx={{ fontFamily: "monospace", mt: 0.5 }}
              />
            ) : (
              <Box
                component="pre"
                sx={{
                  background: "rgba(255,255,255,0.04)",
                  p: 1.5,
                  borderRadius: 1,
                  fontSize: 13,
                  overflowX: "auto",
                  mt: 0.5,
                }}
              >
                {JSON.stringify(approval.tool_call.arguments, null, 2)}
              </Box>
            )}
          </Box>

          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        {!editing ? (
          <Button onClick={() => setEditing(true)} disabled={submitting}>
            Edit
          </Button>
        ) : (
          <Button onClick={() => setEditing(false)} disabled={submitting}>
            Cancel Edit
          </Button>
        )}
        <Box sx={{ flexGrow: 1 }} />
        <Button color="error" onClick={() => handleDecision("rejected")} disabled={submitting}>
          Reject
        </Button>
        {editing ? (
          <Button
            variant="contained"
            color="primary"
            onClick={() => handleDecision("edited")}
            disabled={submitting}
          >
            Approve Edited
          </Button>
        ) : (
          <Button
            variant="contained"
            color="success"
            onClick={() => handleDecision("approved")}
            disabled={submitting}
          >
            Approve
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
