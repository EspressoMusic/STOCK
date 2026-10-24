import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../index.css";
import "./trainer.css";
import TrainerApp from "./TrainerApp.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <TrainerApp />
  </StrictMode>
);
