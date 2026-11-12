# Hidden radio inputs need visible disabled feedback

Disabling an invisible radio input prevents a native second choice, but users
see its label and span. Shared disabled styling must follow the input's state,
not only a structural class applied at initial render. Put pending text and
motion beside the action. Preserve intrinsic disabled controls when recovering
from errors, and keep accepted actions locked until handoff. Capture FormData
before disabling the form or it silently loses values.
