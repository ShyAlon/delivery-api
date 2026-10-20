// The avatar menu on the operator pages: the same account menu and Debug dialog as the app, built from the app's own identity and dialog code.
import {createIdentity} from './identity.js';
import {createDialogs} from './dialogs.js';
const ctx={};
for(const create of [createIdentity,createDialogs])Object.assign(ctx,create(ctx));
ctx.loadIdentity().catch(()=>{});
