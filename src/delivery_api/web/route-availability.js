// The customer page uses a fixed vocabulary, never arbitrary readiness text.
export function routeAvailabilityMessage(route){
 return route.id==='ollama'?'Local interviews are temporarily unavailable. Please try again later.':'DeepSeek is temporarily unavailable. Please try again later.';
}
