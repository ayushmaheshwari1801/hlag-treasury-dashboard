/** hlag_engine runs open the row Methodology view; other runs keep the cash-flow detail drawer. */
export const methodologyOn = (calc: any): boolean => !!calc?.run_id && calc?.engine_status !== undefined;
