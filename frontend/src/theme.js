/**
 * theme.js
 * =========
 * Why this file exists: centralizes the dark theme palette/typography so
 * every component gets a consistent look via MUI's ThemeProvider, per the
 * FRONTEND requirement "Dark Theme".
 */
import { createTheme } from "@mui/material/styles";

const theme = createTheme({
  palette: {
    mode: "dark",
    background: {
      default: "#0f1115",
      paper: "#161a21",
    },
    primary: {
      main: "#6fb3ff",
    },
    secondary: {
      main: "#8b7bff",
    },
    success: { main: "#4caf7d" },
    warning: { main: "#e0a94f" },
    error: { main: "#e0605f" },
    divider: "rgba(255,255,255,0.08)",
  },
  shape: {
    borderRadius: 10,
  },
  typography: {
    fontFamily: '"Inter", "Segoe UI", Roboto, sans-serif',
    h6: { fontWeight: 600 },
  },
  components: {
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
        },
      },
    },
  },
});

export default theme;
