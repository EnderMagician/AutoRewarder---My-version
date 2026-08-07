/* Browser-independent eligibility rules for the main dispatch controls. */

function getControlState(state) {
  const accounts = Array.isArray(state && state.accounts) ? state.accounts : [];
  const currentAccountId = state && state.currentAccountId;
  const current = accounts.find((account) => account.id === currentAccountId);
  const busy = Boolean(state && (state.driverWarmingUp || state.balanceFetching));
  const batchRunning = Boolean(state && state.batchRunning);
  const hasReadyAccount = accounts.some((account) => account.first_setup_done);

  return {
    canStartSelected: Boolean(current && current.first_setup_done) && !busy && !batchRunning,
    canRunAll: hasReadyAccount && !busy && !batchRunning,
    hasReadyAccount,
  };
}

const exported = { getControlState };

if (typeof module !== "undefined" && module.exports) {
  module.exports = exported;
}

if (typeof window !== "undefined") {
  window.AutoRewarderControlState = exported;
}
