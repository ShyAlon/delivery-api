/* Compatibility names share the Page lifecycle, not a second implementation. */
import {Page} from './page-component.js';
export function onPageExit(fn){Page.onExit(fn)}
export function pageEpoch(){return Page.epoch}
export function pageStale(at){return at!==Page.epoch}
export function pageController(){return Page.controller()}
export function leavePage(){Page.leave()}
