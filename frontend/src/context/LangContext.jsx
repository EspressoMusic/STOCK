import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { translations } from "../i18n/translations";

const LangContext = createContext(null);

// Hebrew is the default for everyone; English only when picked in the app.
// (The key moved from "lang" because older builds auto-saved "en" for any
// English-language browser, which would otherwise keep overriding the default.)
const STORAGE_KEY = "lang-v2";

function getInitialLang() {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "en" ? "en" : "he";
}

function resolve(dict, path) {
  return path.split(".").reduce((acc, key) => (acc && acc[key] !== undefined ? acc[key] : undefined), dict);
}

export function LangProvider({ children }) {
  const [lang, setLang] = useState(getInitialLang);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, lang);
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "en" ? "ltr" : "rtl";
  }, [lang]);

  const t = useMemo(() => {
    return (path) => {
      const value = resolve(translations[lang], path);
      if (value !== undefined) return value;
      const fallback = resolve(translations.he, path);
      return fallback !== undefined ? fallback : path;
    };
  }, [lang]);

  const value = useMemo(() => ({ lang, setLang, t }), [lang, t]);

  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useLang() {
  const ctx = useContext(LangContext);
  if (!ctx) throw new Error("useLang must be used within LangProvider");
  return ctx;
}
