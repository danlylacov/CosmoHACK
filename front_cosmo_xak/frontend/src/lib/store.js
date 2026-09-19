import { defaultQuery } from "./validate.js";

const listeners = new Set();

function getViewFromHash() {
  return location.hash === "#/advanced" ? "advanced" : "simple";
}

export const store = {
  state: {
    view: getViewFromHash(),
    query: defaultQuery("historical"),
    errors: {},
    loadStatus: "idle",
    errorMessage: null,
    errorCode: null,
    data: null,
    previousQuery: null,
    previousData: null,
    sources: null,
    tle: null,
    globeTime: 0,
    timePlaying: false,
    timeSpeed: 1,
    timePauseReason: null,
    layers: { radiation: false, mmod: true, illumination: true },
    debrisVisible: {},
    sourceControls: {},
    sourcesLoadStatus: "idle",
    autoRefreshNote: null,
  },

  subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },

  get() {
    return this.state;
  },

  set(patch) {
    const prev = this.state;
    this.state = { ...this.state, ...patch };
    for (const fn of listeners) fn(this.state, prev);
  },

  patchQuery(partial) {
    this.set({ query: { ...this.state.query, ...partial }, errors: {} });
  },
};

export function syncViewFromHash() {
  const view = getViewFromHash();
  if (view !== store.state.view) store.set({ view });
}

export function navigate(view) {
  location.hash = view === "advanced" ? "#/advanced" : "#/simple";
}
