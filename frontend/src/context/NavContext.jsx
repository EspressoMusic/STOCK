import { createContext, useContext, useMemo, useState } from "react";

const NavContext = createContext(null);

export function NavProvider({ children, initialTab = "portfolio" }) {
  const [tab, setTab] = useState(initialTab);
  const value = useMemo(() => ({ tab, goTo: setTab }), [tab]);
  return <NavContext.Provider value={value}>{children}</NavContext.Provider>;
}

export function useNav() {
  const ctx = useContext(NavContext);
  if (!ctx) throw new Error("useNav must be used within NavProvider");
  return ctx;
}
