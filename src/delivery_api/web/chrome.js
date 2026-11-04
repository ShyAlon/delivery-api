// The operator views. The persistent shell (spa-shell.js) adds these to the navigation for
// administrators and marks the current page; the old per-page header/footer injection is gone.
export const OPERATOR_PAGES=[
  {view:'health',label:'Server Health',tip:'Uptime, runs, CPU, memory and who is signed in.'},
  {view:'stops',label:'Admin Stops',tip:'Every time a run was stopped, by whom, and whose run it was.'},
  {view:'spend',label:'Credits And Spend',tip:'Credits left and used for every account.'},
  {view:'grant',label:'Grant Credits',tip:'Give credits to an account and see the recent grants.'},
];
