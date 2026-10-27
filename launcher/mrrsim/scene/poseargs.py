"""Where the vehicle spawns, chosen with script arguments:  --airsim --position X Y Z --rotation ROLL PITCH YAW

The student types them in the Run tab's "Script arguments" box. They are for the launcher, not for the script: `extract`
takes them out of the argument list before the script gets it, and the scene generator (scenegen.py) places the vehicle.
They only count together with --airsim (which the script still receives). Units: metres and degrees, in the simulator's
NED frame (X forward, Y right, Z down: a negative Z is above the ground), like the Vehicles tab's position and rotation.

    --position X Y Z                          where vehicle 0 spawns (also "X,Y,Z" or "X Y Z" as one argument)
    --rotation ROLL PITCH YAW                 which way it faces; every vehicle without its own pose gets it
    --vehicle-pose N=X,Y,Z,ROLL,PITCH,YAW    vehicle N (counted from 0) explicitly; repeatable; wins over the spacing

The other vehicles keep the spacing along Y they have without these arguments, measured from vehicle 0's pose.
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


class PoseError(ValueError):
    """The pose arguments are not usable; the message says what to type instead."""


Vec3 = tuple[float, float, float]
Pose6 = tuple[float, float, float, float, float, float]


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

    def describe(self, count: int) -> list[str]:
        """One line per vehicle (up to 10) saying where it is placed, for the launcher's output."""
        lines = []
        for index in range(min(count, 10)):
            if index in self.vehicles:
                x, y, z, roll, pitch, yaw = self.vehicles[index]
                lines.append(f"vehicle {index} placed at {fmt(x)} {fmt(y)} {fmt(z)}, rotation {fmt(roll)} {fmt(pitch)} {fmt(yaw)}")
                continue
            where = "the scene's position" if self.position is None else (
                f"{fmt(self.position[0])} {fmt(self.position[1])} {fmt(self.position[2])}" if index == 0
                else "spaced from vehicle 0's position")
            turn = "the scene's rotation" if self.rotation is None else " ".join(fmt(v) for v in self.rotation)
            lines.append(f"vehicle {index} placed at {where}, rotation {turn}")
        if count > 10:
            lines.append(f"... and {count - 10} more vehicle(s), spaced the same way")
        return lines


def fmt(value: float) -> str:
    return f"{value:.9g}"


def normalise_angle(degrees: float) -> float:
    """Any finite angle as -180..180."""
    wrapped = (degrees + 180.0) % 360.0 - 180.0
    return 180.0 if wrapped == -180.0 and degrees > 0 else wrapped


def _number(text: str, what: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise PoseError(f"{what}: {text!r} is not a number") from None
    if not math.isfinite(value):
        raise PoseError(f"{what}: {text!r} is not a finite number")
    return value


def _three(args: list[str], at: int, option: str) -> tuple[Vec3, int]:
    """The three numbers after args[at] (separate arguments, or one argument like "1 2 3" or "1,2,3"), and the next index."""
    usage = f"{option} needs three numbers"
    rest = args[at + 1:]
    if not rest:
        raise PoseError(usage)
    if re.search(r"[\s,]", rest[0]):
        parts, used = [p for p in re.split(r"[\s,]+", rest[0].strip()) if p], 1
    else:
        parts, used = rest[:3], 3
    if len(parts) != 3:
        raise PoseError(f"{usage}, for example {option} 1 2 3 (got {len(parts)})")
    values = tuple(_number(p, option) for p in parts)
    return values, at + 1 + used  # type: ignore[return-value]


def _vehicle_pose(text: str) -> tuple[int, Pose6]:
    match = re.fullmatch(r"\s*(\d+)\s*=(.*)", text)
    if not match:
        raise PoseError(f"{VEHICLE_POSE} {text!r}: expected N=x,y,z,roll,pitch,yaw, for example {VEHICLE_POSE} 1=5,3,-2,0,0,90")
    parts = [p for p in re.split(r"[\s,]+", match.group(2).strip()) if p]
    if len(parts) != 6:
        raise PoseError(f"{VEHICLE_POSE} {text!r}: needs six numbers after the vehicle number (x,y,z,roll,pitch,yaw), got {len(parts)}")
    numbers = [_number(p, f"{VEHICLE_POSE} {text!r}") for p in parts]
    pose = (*numbers[:3], *(normalise_angle(a) for a in numbers[3:]))
    return int(match.group(1)), pose  # type: ignore[return-value]


def extract(args: list[str], vehicles: int = 1) -> tuple[list[str], Pose]:
    """Take the pose options out of the script arguments. Returns (the arguments the script gets, the pose); the pose is
    falsy when none was given and then the arguments come back unchanged. Raises PoseError for anything unusable."""
    if not any(a.split("=", 1)[0] in OPTIONS for a in args):
        return list(args), Pose()
    position = rotation = None
    explicit: dict[int, Pose6] = {}
    kept: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        name, equals, inline = arg.partition("=")
        if name in (POSITION, ROTATION):
            if (name == POSITION and position is not None) or (name == ROTATION and rotation is not None):
                raise PoseError(f"{name} was given twice")
            if equals:
                values, i = _three([name, inline], 0, name)[0], i + 1
            else:
                values, i = _three(args, i, name)
            if name == POSITION:
                position = values
            else:
                rotation = tuple(normalise_angle(a) for a in values)
        elif name == VEHICLE_POSE:
            if equals:                                   # --vehicle-pose=1=5,3,-2,0,0,90
                text, i = inline, i + 1
            elif i + 1 < len(args):
                text, i = args[i + 1], i + 2
            else:
                raise PoseError(f"{VEHICLE_POSE} needs N=x,y,z,roll,pitch,yaw")
            number, pose = _vehicle_pose(text)
            if number in explicit:
                raise PoseError(f"{VEHICLE_POSE} was given twice for vehicle {number}")
            if number >= vehicles:
                raise PoseError(f"{VEHICLE_POSE} {text!r}: there is no vehicle {number} (this run has {vehicles}, numbered from 0)")
            explicit[number] = pose
        else:
            kept.append(arg)
            i += 1
    if AIRSIM_FLAG not in kept:
        raise PoseError("--position, --rotation and --vehicle-pose need --airsim")
    return kept, Pose(position, rotation, explicit)  # type: ignore[arg-type]
