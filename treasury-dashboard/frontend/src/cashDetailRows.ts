/** Match the original dashboard's fixed-order native/FX attribution and selected horizon. */
export function cashDetailRows(lines: any[], rowId: string, horizonEnd?: string) {
  const fxRow = rowId.startsWith("fx_");
  const owner = rowId === "interest" ? "rates" : rowId;
  return lines.filter(line => !horizonEnd || line.date <= horizonEnd).flatMap(line => {
    const native = (line.amount_native_scenario_m - line.amount_native_base_m) * line.fx_rate_base;
    let currency = line.currency;
    if (line.owner === "fx" && currency === "USD") {
      currency = ["EUR", "CAD", "GBP", "CNY", "INR", "BRL"].find(c => line.sub_id?.endsWith(":" + c))
        ?? (line.sub_id?.includes("dividend") ? "EUR" : "OTHER");
    }
    const matches = fxRow ? rowId === "fx_eur" ? !["GBP", "CNY", "CAD"].includes(currency) : currency === rowId.slice(3).toUpperCase() : line.owner === owner;
    if (!matches) return [];
    const attributed = fxRow ? line.owner === "fx" ? line.delta_usd_m : line.delta_usd_m - native : native;
    return [{ ...line, attributed_usd_m: attributed }];
  });
}
