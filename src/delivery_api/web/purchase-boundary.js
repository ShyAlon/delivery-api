// This checkout stays offline until a separately reviewed payment rollout.
// No HTTP payment calls, owner selection, provider rates or fixture balances.
export const closedPaymentBoundary=Object.freeze({
 async account(){return {checkout_available:false,balance_credits:null}},
 async create(){throw new Error('Checkout is not available yet')},
 async status(){throw new Error('Payment status is unavailable here')},
 async receipt(){throw new Error('Receipt is unavailable here')},
});
