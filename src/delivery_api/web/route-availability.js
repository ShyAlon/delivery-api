// The customer page uses a fixed vocabulary, never arbitrary readiness text.
export function routeAvailabilityMessage(route){
 if(route.reason==='no_approved_model_pin')return route.operator_repair?'No approved model pin. Approve one in Back office > Model pins.':'No approved model pin. Ask the operator to approve one.';
 if(route.reason==='runtime_unavailable')return 'The execution runtime is not ready. Ask the operator to check startup evidence.';
 return route.id==='ollama'?'Local interviews are temporarily unavailable. Please try again later.':'DeepSeek is temporarily unavailable. Please try again later.';
}
