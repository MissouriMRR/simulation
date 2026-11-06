"""Where the vehicle spawns, chosen with script arguments:  --airsim --position X Y Z --rotation ROLL PITCH YAW

The student types them in the Run tab's "Script arguments" box. They are for the launcher, not for the script: `extract`
takes them out of the argument list before the script gets it, and the scene generator (scenegen.py) places the vehicle.
They only count together with --airsim (which the script still receives). Units: metres and degrees, in the simulator's
NED frame (X forward, Y right, Z down: a negative Z is above the ground), like the Vehicles tab's position and rotation.

    --position X Y Z                          where vehicle 0 spawns (also "X,Y,Z" or "X Y Z" as one argument)
    --rotation ROLL PITCH YAW                 which way it faces; every vehicle without its own pose gets it
    --vehicle-pose N=X,Y,Z,ROLL,PITCH,YAW    vehicle N (counted from 0) explicitly; repeatable; wins over the spacing

The other vehicles keep the spacing along Y they have without these arguments, measured from --position (or from the
scene's own position when there is none).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field

AIRSIM_FLAG = "--airsim"
POSITION, ROTATION, VEHICLE_POSE = "--position", "--rotation", "--vehicle-pose"
OPTIONS = (POSITION, ROTATION, VEHICLE_POSE)
DESCRIBE_LIMIT = 10  # vehicles `Pose.describe` lists one by one; the rest are summed up in a line


class PoseError(ValueError):
    """The pose arguments are not usable; the message says what to type instead."""


Vec3 = tuple[float, float, float]
Pose6 = tuple[float, float, float, float, float, float]


def fmt(value: float) -> str:
    """A number as it is written in the scene file and in the launcher's output: 5.0 as "5", at most 9 digits."""
    return f"{value:.9g}"


@dataclass(frozen=True)
class Pose:
    position: Vec3 | None = None                 # vehicle 0 (and the start of the spacing); None: the scene's
    rotation: Vec3 | None = None                 # roll, pitch, yaw in degrees for every vehicle without its own pose
    vehicles: dict[int, Pose6] = field(default_factory=dict)   # explicit poses by vehicle number

    def __bool__(self) -> bool:
        return self.position is not None or self.rotation is not None or bool(self.vehicles)

    def tag(self) -> str:
        """A short name part for the generated scene file: the same pose always gives the same name, another pose another."""
        text = json.dumps([self.position, self.rotation, sorted(self.vehicles.items())])
        return "pose" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]

    def _describe_one(self, index: int) -> str:
        """The launcher's line for vehicle `index`: where it is placed and which way it faces."""
        if index in self.vehicles:
            x, y, z, roll, pitch, yaw = self.vehicles[index]
            where, turn = f"placed at {fmt(x)} {fmt(y)} {fmt(z)}", " ".join(fmt(v) for v in (roll, pitch, yaw))
        else:
            start = "the scene's position" if self.position is None else " ".join(fmt(v) for v in self.position)
            where = f"placed at {start}" if index == 0 else f"spaced from {start}"
            turn = "the scene's rotation" if self.rotation is None else " ".join(fmt(v) for v in self.rotation)
        return f"vehicle {index} {where}, rotation {turn}"

    def describe(self, count: int) -> list[str]:
        """One line per vehicle saying where it is placed, for the launcher's output: the first DESCRIBE_LIMIT and every
        vehicle with a pose of its own, then one line for the rest."""
        shown = [i for i in range(count) if i < DESCRIBE_LIMIT or i in self.vehicles]
        lines = [self._describe_one(i) for i in shown]
        if count > len(shown):
            lines.append(f"... and {count - len(shown)} more vehicle(s), spaced the same way")
        return lines


def normalise_angle(degrees: float) -> float:
    """Any finite angle as -180..180."""
    wrapped = (degrees + 180.0) % 360.0 - 180.0
    return 180.0 if wrapped == -180.0 and degrees > 0 else wrapped


def _wrapped(angles: Vec3) -> Vec3:
    """Roll, pitch and yaw, each as -180..180."""
    roll, pitch, yaw = angles
    return normalise_angle(roll), normalise_angle(pitch), normalise_angle(yaw)


def _number(text: str, what: str) -> float:
    """`text` as a finite number; PoseError, naming `what` it was for, when it is not one."""
    try:
        value = float(text)
    except ValueError:
        raise PoseError(f"{what}: {text!r} is not a number") from None
    if not math.isfinite(value):
        raise PoseError(f"{what}: {text!r} is not a finite number")
    return value


def _three(args: list[str], at: int, option: str, inline: str | None) -> tuple[Vec3, int]:
    """The three numbers of `option`, which is args[at]. They follow it as separate arguments or as one argument like
    "1 2 3" or "1,2,3", or they are `inline`, the text after an equals sign. Also returns the index of the next argument."""
    usage = f"{option} needs three numbers"
    after = args[at + 1:] if inline is None else [inline]
    if not after:
        raise PoseError(usage)
    if re.search(r"[\s,]", after[0]):
        parts, used = [p for p in re.split(r"[\s,]+", after[0].strip()) if p], 1
    else:
        parts, used = after[:3], 3
    if len(parts) != 3:
        raise PoseError(f"{usage}, for example {option} 1 2 3 (got {len(parts)})")
    x, y, z = (_number(p, option) for p in parts)
    return (x, y, z), at + 1 + (0 if inline is not None else used)


def _vehicle_pose_text(args: list[str], at: int, inline: str | None) -> tuple[str, int]:
    """The N=x,y,z,roll,pitch,yaw of the --vehicle-pose at args[at], from after its equals sign (`inline`) or the next
    argument, and the index of the next argument."""
    if inline is not None:                           # --vehicle-pose=1=5,3,-2,0,0,90
        return inline, at + 1
    if at + 1 < len(args):
        return args[at + 1], at + 2
    raise PoseError(f"{VEHICLE_POSE} needs N=x,y,z,roll,pitch,yaw")


def _vehicle_pose(text: str) -> tuple[int, Pose6]:
    """The vehicle number and pose of the text "N=x,y,z,roll,pitch,yaw" (angles as -180..180)."""
    match = re.fullmatch(r"\s*(\d+)\s*=(.*)", text)
    if not match:
        raise PoseError(f"{VEHICLE_POSE} {text!r}: expected N=x,y,z,roll,pitch,yaw, for example {VEHICLE_POSE} 1=5,3,-2,0,0,90")
    parts = [p for p in re.split(r"[\s,]+", match.group(2).strip()) if p]
    if len(parts) != 6:
        raise PoseError(f"{VEHICLE_POSE} {text!r}: needs six numbers after the vehicle number (x,y,z,roll,pitch,yaw), got {len(parts)}")
    x, y, z, roll, pitch, yaw = (_number(p, f"{VEHICLE_POSE} {text!r}") for p in parts)
    return int(match.group(1)), (x, y, z, normalise_angle(roll), normalise_angle(pitch), normalise_angle(yaw))


def extract(args: list[str], vehicles: int = 1) -> tuple[list[str], Pose]:
    """Take the pose options out of the script arguments. Returns (the arguments the script gets, the pose); the pose is
    falsy when none was given and then the arguments come back unchanged. Raises PoseError for anything unusable."""
    vectors: dict[str, Vec3] = {}                    # --position and --rotation, by option
    explicit: dict[int, Pose6] = {}
    kept: list[str] = []
    i = 0
    while i < len(args):
        name, equals, text = args[i].partition("=")
        inline = text if equals else None
        if name in (POSITION, ROTATION):
            if name in vectors:
                raise PoseError(f"{name} was given twice")
            vectors[name], i = _three(args, i, name, inline)
        elif name == VEHICLE_POSE:
            text, i = _vehicle_pose_text(args, i, inline)
            number, pose = _vehicle_pose(text)
            if number in explicit:
                raise PoseError(f"{VEHICLE_POSE} was given twice for vehicle {number}")
            if number >= vehicles:
                raise PoseError(f"{VEHICLE_POSE} {text!r}: there is no vehicle {number} (this run has {vehicles}, numbered from 0)")
            explicit[number] = pose
        else:
            kept.append(args[i])
            i += 1
    if (vectors or explicit) and AIRSIM_FLAG not in kept:
        raise PoseError("--position, --rotation and --vehicle-pose need --airsim")
    rotation = vectors.get(ROTATION)
    return kept, Pose(vectors.get(POSITION), None if rotation is None else _wrapped(rotation), explicit)
