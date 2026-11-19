import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./tv.css";
import TvApp from "./TvApp.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <TvApp />
  </StrictMode>
);
