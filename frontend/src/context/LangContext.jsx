import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { translations } from "../i18n/translations";

const LangContext = createContext(null);

function getInitialLang() {
  const stored = localStorage.getItem("lang");
  if (stored === "he" || stored === "en") return stored;
  return navigator.language?.startsWith("en") ? "en" : "he";
}

function resolve(dict, path) {
  return path.split(".").reduce((acc, key) => (acc && acc[key] !== undefined ? acc[key] : undefined), dict);
}

export function LangProvider({ children }) {
  const [lang, setLang] = useState(getInitialLang);

  useEffect(() => {
    localStorage.setItem("lang", lang);
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
