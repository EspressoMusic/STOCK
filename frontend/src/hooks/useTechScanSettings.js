import { useEffect, useState } from "react";

const EXTRA_SCANS_KEY = "tech-extra-scans-enabled";

function readExtraScansEnabled() {
  return localStorage.getItem(EXTRA_SCANS_KEY) === "true";
}

export default function useTechScanSettings() {
  const [extraScansEnabled, setExtraScansEnabled] = useState(readExtraScansEnabled);

  useEffect(() => {
    localStorage.setItem(EXTRA_SCANS_KEY, String(extraScansEnabled));
  }, [extraScansEnabled]);

  return {
    extraScansEnabled,
    toggleExtraScans: () => setExtraScansEnabled((prev) => !prev),
  };
}
