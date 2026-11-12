// The customer page uses a fixed vocabulary, never arbitrary readiness text.
export function routeAvailabilityMessage(route){
 if(route.reason==='registry_model_unavailable')return route.error||'No compatible model is ready. Choose another model.';
 if(route.reason==='no_approved_model_pin')return 'No approved interview model is configured. Ask the operator to set one up.';
 if(route.reason==='runtime_unavailable')return 'The execution runtime is not ready. Ask the operator to check startup evidence.';
 return route.id==='ollama'?'Local interviews are temporarily unavailable. Please try again later.':'DeepSeek is temporarily unavailable. Please try again later.';
}
