import { useCallback, useEffect, useState } from "react";
import apiService from "../services/api";
import type { AppSettings } from "../services/types";

interface UseSettingsResult {
  settings: AppSettings | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  setRefreshInterval: (seconds: number | null) => Promise<void>;
  setRisk: (capital: number, riskPerTradePct: number) => Promise<void>;
}

export function useSettings(): UseSettingsResult {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiService
      .getSettings()
      .then(setSettings)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load settings"))
      .finally(() => setLoading(false));
  }, []);

  const save = useCallback(async (action: () => Promise<AppSettings>, failure: string) => {
    setSaving(true);
    setError(null);
    try {
      setSettings(await action());
    } catch (err) {
      setError(err instanceof Error ? err.message : failure);
    } finally {
      setSaving(false);
    }
  }, []);

  const setRefreshInterval = useCallback(
    (seconds: number | null) => save(() => apiService.setRefreshInterval(seconds), "Failed to update refresh interval"),
    [save]
  );

  const setRisk = useCallback(
    (capital: number, riskPerTradePct: number) =>
      save(() => apiService.setRiskSettings(capital, riskPerTradePct), "Failed to update risk settings"),
    [save]
  );

  return { settings, loading, saving, error, setRefreshInterval, setRisk };
}
