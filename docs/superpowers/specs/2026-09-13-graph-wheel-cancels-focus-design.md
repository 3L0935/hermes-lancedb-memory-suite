# Graph Wheel Cancels Focus — Design

## Problem

The graph camera is currently driven by animated `fitGraph()` calls after graph
loads and selection closure. A wheel event can change the scale briefly while one
of those animations is active, but the animation then overwrites that scale. This
makes wheel zoom look responsive for an instant without preserving the zoom.

## Desired Behavior

- Clicking a graph node is the only post-initialization action that automatically
  moves or scales the camera.
- Clicking empty canvas, closing a memory, changing graph mode or filters, and
  reloading graph data must preserve the current camera.
- A wheel event during an animated node focus must cancel that animation at the
  camera's current position and scale, then continue with the normal vis-network
  wheel zoom.
- The one-time vis-network framing performed while the initial layout stabilizes
  remains unchanged so the graph opens in a usable view.

## Implementation

Remove the explicit `fitGraph()` calls from graph loading and selection closure,
then remove the unused helper. Keep `focusNode()` as the sole explicit camera
movement.

Register a capture-phase `wheel` listener on `#graph-canvas`. When a network
exists, read its current view position and scale and call `network.moveTo()` with
those values and `animation: false`. Running before vis-network's wheel handler
stops an in-flight focus animation without preventing or duplicating the wheel
zoom that follows.

Increment the graph script cache-buster in `index.html` so deployed browsers load
the corrected interaction code immediately.

## Verification

- Add a source-level regression test asserting that load and close paths no longer
  fit the camera and that the wheel cancellation uses the public vis-network API.
- Run the targeted visualizer test suite.
- In the browser, start a node focus, send a wheel event before it finishes, and
  verify that the resulting scale remains stable after the former animation would
  have completed.
- Verify that clicking a node still focuses it and that closing the sidebar does
  not change the camera.
