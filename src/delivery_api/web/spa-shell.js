import {render,html,useState,useEffect} from './spa-vendor.js';
/* Compatibility entry. The Page component owns persistent shell construction and styles. */
import {Page} from './page-component.js';
export function mountShell(config){Page.renderer({render,html,useState,useEffect});return Page.mountShell(config)}
