/**
 * API contracts — source of truth for request/response shapes.
 * Runtime is vanilla JS; this file is the typed contract for backend + frontend.
 */

export type Mode = "current" | "historical";
export type HistoricalIntent = "review" | "replay";
export type AnalyzeStatus = "success" | "partial" | "error";
export type SourceStatus = "ok" | "warn" | "error" | "stale";
export type ConfidenceRu = "низкая" | "средняя" | "высокая";
export type ConfidenceEn = "low" | "medium" | "high";
export type WarningType = "observation" | "forecast" | "computed";
export type WarningMechanism = "radiation" | "mmod" | "illumination";
export type MechanismAvailability = "ok" | "insufficient_data" | "disabled";
export type GeometryKind = "operational" | "reconstruction";

export type AnalyzeRequest = {
  mode: Mode;
  start: string;
  duration: number;
  period: number;
  historical_intent: HistoricalIntent | null;
  cutoff_time: string | null;
  disabled_sources: string[];
  frozen_sources: string[];
};

export type Warning = {
  id: string;
  type: WarningType;
  mechanism: WarningMechanism;
  title: string;
  source: string;
  source_url: string | null;
  published_at: string;
  event_time: string | null;
  period: { start: string; end: string } | null;
  value: number | null;
  unit: string | null;
  impact: string;
  rule: string;
  limitations: string;
  confidence: ConfidenceEn;
  intersects_window: boolean;
};

export type TrajectoryPoint = {
  t: string;
  lat: number;
  lon: number;
  alt_km: number;
  in_shadow: boolean;
};

export type SourceRecord = {
  name: string;
  url: string;
  fetched_at: string;
  age: string;
  status: SourceStatus;
  enabled: boolean;
  frozen: boolean;
  unsuitable_for_replay: boolean;
};

export type AnalyzeResponse = {
  query_id: string;
  status: AnalyzeStatus;
  algorithm_version: string;
  request: AnalyzeRequest;
  orbit: {
    tle_source: string;
    tle_epoch: string;
    tle_age_hours: number;
    geometry_kind: GeometryKind;
    iss_trajectory: TrajectoryPoint[];
    shadow_intervals: Array<[string, string]>;
  };
  risk_sw: {
    risk_sw: number | null;
    availability: MechanismAvailability;
    components: { sep: number | null; cme: number | null; kp: number | null; flare: number | null };
    time_series: Array<{ t: number; risk: number }>;
    metrics: { kp: number | null; sep_10mev: number | null; cme_speed: number | null };
    warnings: Warning[];
  };
  risk_mmod: {
    risk_mmod: number | null;
    availability: MechanismAvailability;
    components: { conjunction: number | null; meteor: number | null };
    time_series: Array<{ t: number; risk: number }>;
    metrics: { miss_distance: number | null; pc: number | null; tca: string | null };
    warnings: Warning[];
  };
  windows: Array<{
    start: string;
    end: string;
    score: number | null;
    risk_sw: number | null;
    risk_mmod: number | null;
    overlap_sw: number | null;
    overlap_mmod: number | null;
    completeness: number;
    requires_check: boolean;
  }>;
  recommendation: {
    window: { start: string; end: string } | null;
    score: number | null;
    reason: string;
    limitations: string;
    tie: boolean;
  };
  verification: {
    cutoff_time: string;
    used_in_calculation: false;
    note: string;
    later_observations: Warning[];
  } | null;
  metadata: {
    completeness: number;
    confidence_sw: ConfidenceRu;
    confidence_mmod: ConfidenceRu;
    algorithm_version: string;
    sources: SourceRecord[];
  };
};

export type ErrorResponse = {
  status: "error";
  message: string;
  code: string;
  algorithm_version: string;
};

export type SourcesStatusResponse = {
  status: AnalyzeStatus;
  algorithm_version: string;
  completeness: number;
  confidence_sw: ConfidenceRu;
  confidence_mmod: ConfidenceRu;
  refreshed_at: string;
  sources: SourceRecord[];
};

export type TleResponse = {
  status: "success";
  algorithm_version: string;
  name: string;
  norad_id: number;
  source: string;
  epoch: string;
  age_hours: number;
  line1: string;
  line2: string;
};
