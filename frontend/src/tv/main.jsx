import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { setApiBase } from "../api";
import "./tv.css";
import TvApp from "./TvApp.jsx";

// On the live site (a production build) the panel's candles, prices and trades come from the
// site's own functions (frontend/api), whatever server the app's VITE_API_BASE names.
if (import.meta.env.PROD) setApiBase("");

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <TvApp />
  </StrictMode>
);
