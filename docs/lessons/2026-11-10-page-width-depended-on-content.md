# 2026-11-10: the page column was as wide as its content

## What happened
After the 11-10 merge the task page rendered as a ~780px column in the middle of a 1424px window.

## Cause
`body` is a flex row and `main` had `margin:auto` with no width (page-component.js). A flex item with auto margins
shrinks to its content, so page width followed the text on the page. Pages with long lines looked full width;
short, plain-word text (the new run-progress block) made the column narrow. It was latent: `/` was 654px and an
empty task 1188px before any 11-10 change.

## Fix
`main{width:100%;max-width:1500px;margin:auto}`. A browser test asserts main is the viewport width (1424) and the
1500px cap (at 1900) on a task page, an empty task and the dashboard.

## Rule
A layout width is set by the layout, never by the content. Assert widths in a browser at a desktop viewport with short content.
