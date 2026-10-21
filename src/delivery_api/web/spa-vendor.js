/* The vendored framework entry: re-exports Preact + htm so the rest of the app has one import site.
   Files in ./vendor are byte-pinned (see vendor/VENDORED.md); a test recomputes their sha256. */
import {render,h} from './vendor/preact.module.js';
import {useState,useEffect,useRef} from './vendor/hooks.module.js';
import htm from './vendor/htm.module.js';
export {render,h,useState,useEffect,useRef};
export const html=htm.bind(h);
