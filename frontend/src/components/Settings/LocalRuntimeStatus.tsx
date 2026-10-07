import { useEffect, useState } from "react";
import apiService from "../../services/api";
import type { LocalRuntime } from "../../services/types";

const TEXT: Record<LocalRuntime["state"], string> = {
  serving: "Bionic is running and serving the local model.",
  starting: "Starting Bionic…",
  no_server: "Bionic is open but its server isn't answering — load the model and start the server in Bionic.",
  stopped: "Bionic isn't running.",
  error: "",
  unsupported: "",
};

/**
 * Bionic follows the model settings: selecting a Bionic model launches it, dropping it closes it.
 * Shown only while a Bionic model is selected or Bionic is still running; polls faster while it starts.
 */
export default function LocalRuntimeStatus({ refreshKey }: { refreshKey: unknown }) {
  const [rt, setRt] = useState<LocalRuntime | null>(null);

  useEffect(() => {
    let alive = true;
    let timer = 0;
    const load = () =>
      apiService.getLocalRuntime()
        .then((r) => {
          if (!alive) return;
          setRt(r);
          const settling = r.state === "starting" || (r.selected !== r.running) || (r.selected && !r.serving);
          timer = window.setTimeout(load, settling ? 3000 : 20000);
        })
        .catch(() => { if (alive) timer = window.setTimeout(load, 20000); });
    load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [refreshKey]);

  if (!rt || rt.state === "unsupported" || (!rt.selected && !rt.running)) return null;
  const text = rt.state === "error" ? `Bionic: ${rt.error}`
    : !rt.selected && rt.running ? "Closing Bionic — no local model is selected…"
    : TEXT[rt.state];
  const tone = rt.state === "serving" ? "text-neon-emerald" : rt.state === "error" || rt.state === "stopped" ? "text-neon-rose" : "text-amber-300";
  return <p className={`text-xs ${tone}`}>{text}</p>;
}
