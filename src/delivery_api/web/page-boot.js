/* Create the owned outlet before page modules capture their DOM references. */
import {Page} from './page-component.js';
Page.installStyles();
Page.main(document.querySelector('#page-outlet'));
await import('./app.js');
