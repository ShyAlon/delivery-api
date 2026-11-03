
CI source-order coverage also assumed dialog hover CSS stayed in style.css. The component now injects it after global styles, so its close-button hover rule is scoped to enabled buttons. Updated the source test to keep global ordering assertions and separately require the moved rule's enabled-only selector; no disabled-button contract was removed.
