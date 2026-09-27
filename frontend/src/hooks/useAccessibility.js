import { useEffect, useState } from "react";

const FONT_KEY = "a11y-font-scale";
const CONTRAST_KEY = "a11y-high-contrast";
const MOTION_KEY = "a11y-reduce-motion";

const FONT_SCALES = ["normal", "large", "xlarge"];

function readFontScale() {
  const stored = localStorage.getItem(FONT_KEY);
  return FONT_SCALES.includes(stored) ? stored : "normal";
}

function readBool(key) {
  return localStorage.getItem(key) === "1";
}

export default function useAccessibility() {
  const [fontScale, setFontScale] = useState(readFontScale);
  const [highContrast, setHighContrast] = useState(() => readBool(CONTRAST_KEY));
  const [reduceMotion, setReduceMotion] = useState(() => readBool(MOTION_KEY));

  useEffect(() => {
    document.documentElement.setAttribute("data-font-scale", fontScale);
    localStorage.setItem(FONT_KEY, fontScale);
  }, [fontScale]);

  useEffect(() => {
    document.documentElement.setAttribute("data-contrast", highContrast ? "high" : "normal");
    localStorage.setItem(CONTRAST_KEY, highContrast ? "1" : "0");
  }, [highContrast]);

  useEffect(() => {
    document.documentElement.setAttribute("data-motion", reduceMotion ? "reduced" : "full");
    localStorage.setItem(MOTION_KEY, reduceMotion ? "1" : "0");
  }, [reduceMotion]);

  const cycleFontScale = () => {
    setFontScale((prev) => FONT_SCALES[(FONT_SCALES.indexOf(prev) + 1) % FONT_SCALES.length]);
  };

  return {
    fontScale,
    cycleFontScale,
    highContrast,
    toggleHighContrast: () => setHighContrast((v) => !v),
    reduceMotion,
    toggleReduceMotion: () => setReduceMotion((v) => !v),
  };
}
