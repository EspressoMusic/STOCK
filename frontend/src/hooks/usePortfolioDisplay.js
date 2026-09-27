import { useEffect, useState } from "react";

const MODE_KEY = "portfolio-display-mode";

function readMode() {
  const stored = localStorage.getItem(MODE_KEY);
  return stored === "advanced" ? "advanced" : "simple";
}

export default function usePortfolioDisplay() {
  const [mode, setMode] = useState(readMode);

  useEffect(() => {
    localStorage.setItem(MODE_KEY, mode);
  }, [mode]);

  return {
    mode,
    isAdvanced: mode === "advanced",
    setMode,
    toggleMode: () => setMode((prev) => (prev === "advanced" ? "simple" : "advanced")),
  };
}
