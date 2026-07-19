/**
 * components/ChatPanel.jsx
 * ==========================
 * Why this file exists: the primary conversational surface. Sends user
 * messages to `POST /api/chat`, renders the conversation thread, and
 * surfaces per-message tool-call/tool-result status chips so the user can
 * see what the agent did (or attempted) for each turn, per the FRONTEND
 * requirements "Chat", "Tool Status", "Loading Indicators".
 */
import React, { useEffect, useRef, useState } from "react";
import {
  Box,
  TextField,
  IconButton,
  Paper,
  Typography,
  Stack,
  Chip,
  CircularProgress,
  Avatar,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
} from "@mui/material";
import SendIcon from "@mui/icons-material/Send";
import SmartToyIcon from "@mui/icons-material/SmartToy";
import PersonIcon from "@mui/icons-material/Person";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorIcon from "@mui/icons-material/Error";
import { sendChatMessage } from "../api/client.js";

function ToolStatusChips({ toolResults }) {
  if (!toolResults || toolResults.length === 0) return null;
  return (
    <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ mt: 1 }}>
      {toolResults.map((r) => (
        <Chip
          key={r.tool_call_id}
          size="small"
          icon={r.success ? <CheckCircleIcon /> : <ErrorIcon />}
          label={r.tool_name}
          color={r.success ? "success" : "error"}
          variant="outlined"
        />
      ))}
    </Stack>
  );
}

const PROVIDER_MODELS = {
  gemini: ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-1.5-flash"],
  github: ["meta/Llama-3.3-70B-Instruct", "deepseek/DeepSeek-V3", "gpt-4.1"],
  openrouter: ["deepseek/deepseek-chat", "meta-llama/llama-3.3-70b-instruct", "openai/gpt-4o-mini"],
};

// Sequence of pipeline phases shown while a turn is in flight. The
// backend answers with one synchronous HTTP response per turn (no
// streaming), so this does NOT reflect the real-time backend node the
// agent is actually in — it's a client-side approximation of the known
// pipeline order (Intent -> Tool Selection -> Validation -> Approval
// Gate -> Tool Execution -> Response Generation), advanced on a timer so
// the user sees meaningful progress instead of one static "Working..."
// label for the whole request.
const REQUEST_PHASES = ["thinking", "selecting_tool", "validating", "executing", "generating_response"];
const PHASE_INTERVAL_MS = 900;

export default function ChatPanel({ sessionId, onAgentResult, onPhaseChange, pendingApproval }) {
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content:
        "Hi! I can create and manage tasks, save and search notes, generate work plans and weekly reports, and pull action items out of meeting notes. What would you like to do?",
    },
  ]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [llmProvider, setLlmProvider] = useState("gemini");
  const [llmModel, setLlmModel] = useState("gemini-2.0-flash");
  const scrollRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (!PROVIDER_MODELS[llmProvider].includes(llmModel)) {
      setLlmModel(PROVIDER_MODELS[llmProvider][0]);
    }
  }, [llmProvider, llmModel]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || sending || pendingApproval) return;

    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setSending(true);

    let phaseIndex = 0;
    onPhaseChange?.(REQUEST_PHASES[0]);
    const phaseTimer = setInterval(() => {
      phaseIndex = Math.min(phaseIndex + 1, REQUEST_PHASES.length - 1);
      onPhaseChange?.(REQUEST_PHASES[phaseIndex]);
    }, PHASE_INTERVAL_MS);

    try {
      const result = await sendChatMessage(sessionId, text, llmProvider, llmModel);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: result.reply,
          toolResults: result.tool_results,
          status: result.status,
        },
      ]);
      onAgentResult(result);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: "Sorry, something went wrong reaching the agent. Please try again.",
          status: "error",
        },
      ]);
    } finally {
      clearInterval(phaseTimer);
      onPhaseChange?.(null);
      setSending(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Box sx={{ flexGrow: 1, overflowY: "auto", p: 2 }}>
        <Stack spacing={2}>
          {messages.map((m, idx) => (
            <Box key={idx} sx={{ display: "flex", gap: 1.5, alignItems: "flex-start" }}>
              <Avatar
                sx={{
                  width: 32,
                  height: 32,
                  bgcolor: m.role === "user" ? "primary.main" : "secondary.main",
                }}
              >
                {m.role === "user" ? <PersonIcon fontSize="small" /> : <SmartToyIcon fontSize="small" />}
              </Avatar>
              <Paper
                variant="outlined"
                sx={{
                  p: 1.5,
                  maxWidth: "80%",
                  bgcolor: m.role === "user" ? "rgba(111,179,255,0.08)" : "background.paper",
                  borderColor: m.status === "error" ? "error.main" : "divider",
                }}
              >
                <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
                  {m.content}
                </Typography>
                <ToolStatusChips toolResults={m.toolResults} />
              </Paper>
            </Box>
          ))}
          {sending && (
            <Box sx={{ display: "flex", gap: 1.5, alignItems: "center", pl: 5.5 }}>
              <CircularProgress size={16} />
              <Typography variant="caption" color="text.secondary">
                Agent is thinking...
              </Typography>
            </Box>
          )}
          <div ref={scrollRef} />
        </Stack>
      </Box>
      <Box sx={{ p: 2, borderTop: "1px solid", borderColor: "divider" }}>
        <Stack spacing={1.2}>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
            <FormControl size="small" sx={{ minWidth: 150 }}>
              <InputLabel id="llm-provider-select-label">Provider</InputLabel>
              <Select
                labelId="llm-provider-select-label"
                value={llmProvider}
                label="Provider"
                onChange={(e) => setLlmProvider(e.target.value)}
                disabled={sending || Boolean(pendingApproval)}
              >
                <MenuItem value="gemini">Gemini</MenuItem>
                <MenuItem value="github">GitHub Models</MenuItem>
                <MenuItem value="openrouter">OpenRouter</MenuItem>
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 220 }}>
              <InputLabel id="llm-model-select-label">Model</InputLabel>
              <Select
                labelId="llm-model-select-label"
                value={llmModel}
                label="Model"
                onChange={(e) => setLlmModel(e.target.value)}
                disabled={sending || Boolean(pendingApproval)}
              >
                {PROVIDER_MODELS[llmProvider].map((model) => (
                  <MenuItem key={model} value={model}>
                    {model}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Stack>
          <Stack direction="row" spacing={1}>
            <TextField
              fullWidth
              size="small"
              placeholder={
                pendingApproval
                  ? "Resolve the pending approval before sending another message..."
                  : "Ask the agent to create a task, list tasks, save a note..."
              }
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={sending || Boolean(pendingApproval)}
              multiline
              maxRows={4}
            />
            <IconButton
              color="primary"
              onClick={handleSend}
              disabled={sending || !input.trim() || Boolean(pendingApproval)}
            >
              <SendIcon />
            </IconButton>
          </Stack>
        </Stack>
      </Box>
    </Box>
  );
}
