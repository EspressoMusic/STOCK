import { createContext, useContext, useMemo, useState } from "react";
import { CENTER_TAB } from "../features";

const NavContext = createContext(null);

export function NavProvider({ children, initialTab = CENTER_TAB }) {
  const [tab, setTab] = useState(initialTab);
  const [params, setParams] = useState(null);
  const goTo = (nextTab, nextParams = null) => {
    setTab(nextTab);
    setParams(nextParams);
  };
  const value = useMemo(() => ({ tab, params, goTo }), [tab, params]);
  return <NavContext.Provider value={value}>{children}</NavContext.Provider>;
}

export function useNav() {
  const ctx = useContext(NavContext);
  if (!ctx) throw new Error("useNav must be used within NavProvider");
  return ctx;
}
