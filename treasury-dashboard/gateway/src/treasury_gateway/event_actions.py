"""Join existing Event advice to the displayed calculation without inventing forecasts."""
import hashlib
import re
from .news_v8 import modules_for


def event_actions(finance, events, selected_ids=(), forecast_modified=False):
    selected = set(selected_ids)
    candidates = [e for e in events if not selected or e["event_id"] in selected]
    candidates.sort(key=lambda e: (-(e.get("score") or {}).get("total", 0), e["event_id"]))
    liquidity = finance["liquidity"]
    calculation = finance["calculation"]
    def amount(value):
        return f"EUR {value:+,.2f}m" if value is not None else "unavailable"
    actions = [a for a in finance["all_actions"] if a["rule_id"] == "cash_data_missing"]
    seen = {}
    for event in candidates:
        for advice in event.get("what_to_do", []):
            key = re.sub(r"\W+", " ", advice.casefold()).strip()
            if not key:
                continue
            if key in seen:
                previous = seen[key]
                previous["event_ids"].append(event["event_id"])
                previous["article_ids"] = list(dict.fromkeys(previous["article_ids"] + event["article_ids"]))
                continue
            modules = modules_for(advice) or event.get("modules", [])
            module = modules[0] if modules else "context"
            context = "Not quantified in the current scenario. Select this Event to estimate its cash impact."
            severity = "LOW"
            if selected:
                # The full selected scenario includes FX translation and any manual overrides.
                label = "Current edited scenario" if forecast_modified else "Current Event scenario"
                net = finance["cash_impact"]["net_eur_m"]
                headroom = liquidity["scenario_headroom_eur_m"]
                when = liquidity["scenario_min_date"] or "unavailable"
                context = (f"{label}: net cash impact {amount(net)} over {finance['horizon_months']} months; "
                           f"minimum headroom {amount(headroom)} on {when}.")
                if net is None:
                    context += " Required inputs are incomplete; this is not a zero-impact estimate."
                state = calculation["status"]
                severity = state if state in {"LOW", "HIGH", "CRITICAL"} else "LOW"
            article_ids = list(event["article_ids"])
            action = {"rule_id": "event:" + hashlib.sha256((event["event_id"] + key).encode()).hexdigest()[:20],
                      "module": module, "area": event["title"], "text": advice,
                      "context": context, "condition": "Selected Event scenario" if selected else "Event monitoring; cash impact not estimated",
                      "assumptions": ["Event advice is retained from v8; displayed calculations are conditional scenario estimates."],
                      "severity": severity, "kind": "event recommendation", "event_ids": [event["event_id"]],
                      "article_ids": article_ids, "run_id": calculation["run_id"] if selected else None,
                      "quantified": bool(selected)}
            seen[key] = action
            actions.append(action)
    return actions
