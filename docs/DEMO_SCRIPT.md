# 60-second demo (judge wears the Quest)

The judge puts on the Meta Quest 2 and sees people through the cardboard wall.
Everyone else watches the Quest's view cast to a Mac, next to the command map.

## Cast
| Who | Does |
|---|---|
| **Narrator** (lead) | Talks the judge through it, points at the cast screen |
| **Headset handler** (Role 1) | Prepares, hands over and fits the Quest; re-aligns if needed |
| **Walker** (bright top) | Walks the scripted path |
| **Operator** (Role 2) | Watches the command map and server logs on Mac-1 |

## Before the judges arrive (≤ 2 min)
- [ ] Quest charged (>50%). The headset is in the browser on the viewer page, **already in AR** (started with "Enter AR"), with tracks updating
- [ ] Alignment done in the last few minutes: corners touched in order **1 → 7 → 3 → 5**, errors a few cm; virtual corners sit on the real markers
- [ ] Quest casting to a Mac browser (oculus.com/casting), shown big next to the command map
- [ ] Judge spot marked with tape, facing the cardboard wall; Quest boundary covers that spot
- [ ] Quest won't sleep when taken off. Test this beforehand: try
      `adb shell am broadcast -a com.oculus.vrpowermanager.prox_close` (lasts until reboot),
      or keep it on someone's head until handover
- [ ] Wipes / face cover; glasses spacer available
- [ ] Walker at the start position, out of sight behind the wall
- [ ] Fallback ready: iPhone with web/viewer/ open, plus the backup video

## Handover (≈10 s, not counted in the 60 s)
"Pop this on. You'll see the room as normal, just in black and white." Help with the strap.
**Don't press or hold the Meta button**: recentering moves the Quest's coordinate frame and breaks alignment.
Stand the judge on the tape mark, facing the wall.

## The 60 seconds
| Time | Walker | Judge sees in the Quest | Narrator says (roughly) |
|---|---|---|---|
| 0-10 s | Steps out into the open | Thin outline capsule on the walker, locked to them | "Every camera in the room shares one map. That outline is a person the system is tracking." |
| 10-25 s | Walks **behind the cardboard wall** | Glowing **cyan ghost** through the wall, "via camB"; the wall segment lights up | "You can't see them, but the camera behind the wall can. That's their real position, through the wall." |
| 25-40 s | Steps into the **blind spot** (no camera) | **Orange** capsule plus a floor fan that **grows**, "last seen 3 s" | "Now nobody can see them. We predict where they're heading, and the cone shows how unsure we are." (PIR triggers → fan shrinks: "a motion sensor just narrowed it down.") |
| 40-50 s | **Re-emerges where the cone pointed** | Snaps back to a solid outline: reacquired | "And there they are, where we said they'd be." |
| 50-60 s | Waves | (Stretch: a skeleton waves through the wall.) Optional voice: "Contact, behind wall, 4 metres" | "For firefighters and search & rescue: see who's behind the wall before you open the door." |

Take the headset back, then point at the command map: "Here's the same picture from above: cameras, walls, and the uncertainty for every person."

## If something goes wrong
| Problem | Do this |
|---|---|
| Ghosts are offset from the walker | Handler re-aligns (hold A+B 1 s; touch 1 → 7 → 3 → 5) and carries on talking |
| Quest went to sleep or left AR | Handler re-enters AR; it uses the saved alignment |
| Tracks frozen | Operator checks Mac-1 server / network (`tools/net_check.py`) |
| Quest unusable | Hand over the iPhone running web/viewer/ (2D overlay), or play the backup video |
| ID swap / second person in view | Walker keeps moving; only one walker in the area |

## Rehearse (5x before judging)
- Time every run, including the handover.
- At least once, start from a Quest that has just been switched on, to time the full setup + alignment.
- Record the best run as the backup video, with the cast view and the command map side by side.
