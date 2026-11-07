import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
// Scale the whole layout to the window: 1.0 at 1440px wide, larger on big screens, never below 0.85.
// Override with ?scale=1.3 in the address bar (saved), or ?scale=auto to go back to automatic.
function fit() {
  let manual: number | null = null;
  try {
    const q = new URLSearchParams(location.search).get("scale");
    if (q === "auto") localStorage.removeItem("ui_scale");
    else if (q && Number(q) > 0) localStorage.setItem("ui_scale", q);
    const saved = localStorage.getItem("ui_scale"); manual = saved ? Number(saved) : null;
  } catch { /* storage unavailable: automatic scaling */ }
  const auto = Math.min(2, Math.max(0.85, window.innerWidth / 1440));
  (document.documentElement.style as any).zoom = String(manual && manual > 0 ? manual : auto);
}
fit(); window.addEventListener("resize", fit);
createRoot(document.getElementById("root")!).render(<App />);
