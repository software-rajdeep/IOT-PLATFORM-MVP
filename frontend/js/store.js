// Client session + wizard selection. Kept in the browser only; the server
// authorizes every call, so this is purely UX state.
const TOKEN_KEY = "ucap_token";
const SESSION_KEY = "ucap_session";
const SELECTION_KEY = "ucap_selection";

export const store = {
  token: () => localStorage.getItem(TOKEN_KEY),
  session: () => {
    try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null"); }
    catch { return null; }
  },
  setSession: (token, session) => {
    localStorage.setItem(TOKEN_KEY, token);
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  },
  clearSession: () => {
    localStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(SESSION_KEY);
  },

  // Wizard selection (device -> purpose -> template)
  selection: () => {
    try { return JSON.parse(sessionStorage.getItem(SELECTION_KEY) || "null") || {}; }
    catch { return {}; }
  },
  setSelection: (patch) => {
    const next = { ...store.selection(), ...patch };
    sessionStorage.setItem(SELECTION_KEY, JSON.stringify(next));
    return next;
  },
  clearSelection: () => sessionStorage.removeItem(SELECTION_KEY),
};
