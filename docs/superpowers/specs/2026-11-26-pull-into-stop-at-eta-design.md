# Pull the pin into the stop at LTA's arrival time

Short design, 26 Nov 2026.

## Why

In the field test of the fix-age change (#19), pins paused just short of the
watched stop. That is the 80 m hold (`HOLD_M` in `static/glide.js`): the
shadow bus never enters the last 80 m until a real GPS fix lands inside it.
Fixes arrive every 12-60 s and the page polls every 20 s, so the pin often
sits short while the real bus is already pulling in.

## Goal

Once LTA's own arrival time has passed, assume the bus has arrived: the pin
drives the last 80 m onto the stop.

## Non-goals

- Pausing (dwell) at intermediate stops. A separate, larger piece; may come
  later.
- Changing the tile's "Arr" label or its 30 s buffer.

## Anti-goals

- Pins arriving long before the bus. Triggering on the tile's "Arr" would
  do that (up to ~90 s early, because of `BUFFER_SECONDS`), so it is not the
  trigger.

## Approaches

- **A. Release the hold at LTA's unbuffered arrival time.** Picked. "Arr" in
  LTA's own terms. Trade-off: when LTA is optimistic the pin sits on the stop
  20-30 s before the bus.
- B. Release when the tile shows "Arr". Rejected: up to 90 s early.
- C. Release on "Arr" only if a GPS fix is within ~150 m. Rejected: still
  waits for a fix, so the pause the user saw mostly stays.

## Decided

- The hold applies only while `now < tA`. From `tA` on, the shadow may drive
  to the stop, still under the 60 km/h ceiling.
- The visible pin follows with the existing catch-up (~2 s ease, at most
  90 km/h), so 80 m takes a few seconds: a pull-in, not a teleport.
- Applies to the watched stop only (the end of each pin's path).

## Consequences (builder's calls, flagged for review)

- An ETA already due when the fix lands ("Arr" for a bus far out): the shadow
  now drives all the way to the stop at 60 km/h instead of stopping 80 m
  short. Same rule as above, applied consistently.
- LTA revises the ETA later after the pin has pulled in: the hold comes back,
  but pins never reverse, so the pin stays where it is until a fresh fix
  says otherwise (snap if the bus is > 600 m behind).
- "LTA vs actual" panel unchanged: it judges arrival from GPS fixes, not the
  pin.

## Left to the builder

- Tests: replace "holds 80 m short even once the ETA has passed" with
  "holds before tA, reaches the stop after tA".
- README rules table and `docs/glide-logic.excalidraw`: update the hold row
  and box.
- Branch from `main`, separate from PR #20.
