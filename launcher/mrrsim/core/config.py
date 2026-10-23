"""mrrsim.toml loading and validation."""

from __future__ import annotations

import difflib
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from . import ports

# === CONSTANTS ================================================================


# Windows priority classes for the Unreal process, lowest to highest
UNREAL_PRIORITIES = (
    "low",
    "below_normal",
    "normal",
    "above_normal",
    "high",
)


# === ERRORS ===================================================================


class ConfigError(ValueError):
    """A setting is missing, unknown or has a wrong value; the message says
    which and how to fix it."""


# === SIZES ====================================================================


def parse_size_mb(text: str) -> int:
    """'4GB' -> 4096, '2048MB' -> 2048. Raises ConfigError for anything else."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(GB|MB)\s*", text, re.I)
    if not match:
        raise ConfigError(
            f"size must look like '4GB' or '2048MB', got {text!r}"
        )
    value = float(match.group(1)) * (
        1024 if match.group(2).upper() == "GB" else 1
    )
    return int(value)


# === VALUE CHECKS ============================================================


def _is_number(value: Any) -> bool:
    """True for an int or a float; a bool, though Python counts it as an int,
    is not a number here (`true` in the toml is not a setting of 1)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_whole_number(value: Any) -> bool:
    """True for an int; a bool is not."""
    return isinstance(value, int) and not isinstance(value, bool)


# === SETTINGS SECTIONS ========================================================


@dataclass
class WslCfg:
    """The [wsl] section: which distro runs the containers, and the .wslconfig
    limits."""

    distro: str = "mrr"
    require_mirrored: bool = True
    use_init: bool = True
    # .wslconfig [wsl2] memory, e.g. "4GB"; "" = leave WSL's default alone
    memory: str = ""
    processors: int = 0  # .wslconfig [wsl2] processors; 0 = leave alone

    @property
    def memory_mb(self) -> int | None:
        """The memory limit in megabytes, or None when none is set."""
        return parse_size_mb(self.memory) if self.memory else None


@dataclass
class UnrealCfg:
    """The [unreal] section: how Unreal is started and reached."""

    exe: Path = Path(
        "../Windows/Simulation/Binaries/Win64/Simulation-Win64-Shipping.exe"
    )
    # -ResX; also the render resolution, so smaller is cheaper
    width: int = 1280
    height: int = 720  # -ResY
    windowed: bool = True  # -windowed (False: -fullscreen)
    vsync: bool = False  # False adds -NoVSync
    # Windows priority class of the Unreal process
    priority: str = "above_normal"
    # extra arguments, appended as they are
    args: list[str] = field(default_factory=lambda: ["-nosplash"])
    host: str = "127.0.0.1"
    topic_port: int = 8989
    service_port: int = 8990
    ready_timeout_s: float = 120.0
    # how long Unreal gets to close after the close request before it is killed
    close_grace_s: float = 4.0
    # how long to wait for WSL to be able to reach Unreal (firewall prompt);
    # 0 = skip
    wsl_reach_timeout_s: float = 90.0

    @property
    def launch_args(self) -> list[str]:
        """The arguments Unreal is started with: window mode, size, vsync, then
        `args`."""
        out = [
            "-windowed" if self.windowed else "-fullscreen",
            f"-ResX={self.width}",
            f"-ResY={self.height}",
        ]
        if not self.vsync:
            out.append("-NoVSync")
        return out + list(self.args)


@dataclass
class SceneCfg:
    """The [scene] section: the scene to load and how long to wait for it."""

    image: str = "localhost/mrrsim-script:airsim"
    config_dir: Path = Path("../sim_config")
    scene: str = ""
    hold: bool = True
    delay_s: float = 2.0
    load_timeout_s: float = 180.0
    # set from the vehicle design by the launcher, not by mrrsim.toml
    camera_streams: list = field(default_factory=list)


@dataclass
class SitlCfg:
    """The [sitl] section: the ArduPilot flight controller, one per vehicle."""

    ardupilot_ref: str = "Copter-4.7.0"
    image: str = ""
    vehicles: int = 1
    model: str = "airsim-copter"
    home: str = "37.9490953,-91.7848293,0,0"
    speedup: float = 1.0
    sim_address: str = "127.0.0.1"
    gcs_out: str = "127.0.0.1:14550"
    wipe: bool = True
    synthetic_clock: bool = False  # -S is a no-op in ArduPilot 4.7.0
    defaults: str = ""
    # ArduPilot parameter overrides
    params: dict[str, Any] = field(default_factory=dict)
    extra_args: list[str] = field(default_factory=list)
    health_timeout_s: float = 120.0
    stop_timeout_s: int = 5
    # copy ArduPilot's own flight logs (.BIN) out of the container at shutdown
    save_logs: bool = True
    # relative to the folder `mrrsim` is run from; one sub-folder per session
    log_dir: str = "logs"

    @property
    def image_ref(self) -> str:
        """The SITL image to run: `image`, or the one named after the ArduPilot
        version."""
        return (
            self.image or f"localhost/mrrsim-sitl:{self.ardupilot_ref.lower()}"
        )


@dataclass
class ScriptsCfg:
    """The [scripts] section: the student's script and how it runs."""

    image: str = "localhost/mrrsim-script:runtime"
    dir: Path = Path("../scripts")
    main: str = ""
    args: list[str] = field(default_factory=list)
    stop_grace_s: float = 25.0
    # The script may write into its folder: the writes go to a scratch layer and
    # the folder itself stays untouched (the Project AirSim client always writes
    # projectairsim_client.log into its working folder).
    overlay: bool = True


# === THE WHOLE CONFIGURATION ==================================================


@dataclass
class Config:
    """All the settings of a run: the file they came from and one object per
    section."""

    source: Path
    wsl: WslCfg = field(default_factory=WslCfg)
    unreal: UnrealCfg = field(default_factory=UnrealCfg)
    scene: SceneCfg = field(default_factory=SceneCfg)
    sitl: SitlCfg = field(default_factory=SitlCfg)
    scripts: ScriptsCfg = field(default_factory=ScriptsCfg)

    @property
    def base_dir(self) -> Path:
        """The folder of the settings file; relative paths in it are relative to
        this."""
        return self.source.parent

    def validate(self) -> None:
        """Check the values against their limits and each other; raise
        ConfigError listing every problem."""
        errors = [
            *self._sitl_errors(),
            *self._wsl_errors(),
            *self._unreal_errors(),
            *self._argument_list_errors(),
        ]
        if errors:
            raise ConfigError(
                "invalid configuration:\n  " + "\n  ".join(errors)
            )

    def _sitl_errors(self) -> list[str]:
        """The problems with the [sitl] values."""
        errors: list[str] = []
        if not 1 <= self.sitl.vehicles <= ports.MAX_VEHICLES:
            errors.append(
                f"[sitl] vehicles must be 1..{ports.MAX_VEHICLES}, got "
                f"{self.sitl.vehicles}"
            )
        if not re.fullmatch(
            r"-?\d+(\.\d+)?(,-?\d+(\.\d+)?){3}", self.sitl.home
        ):
            errors.append(
                "[sitl] home must be 'lat,lon,alt,heading', got "
                f"{self.sitl.home!r}"
            )
        if not re.fullmatch(r"[^:\s]+:\d{1,5}", self.sitl.gcs_out):
            errors.append(
                "[sitl] gcs_out must be 'host:port' (SERIAL0 must not wait "
                "for a TCP client), "
                f"got {self.sitl.gcs_out!r}"
            )
        if self.sitl.speedup <= 0:
            errors.append("[sitl] speedup must be > 0")
        if not isinstance(self.sitl.params, dict):
            errors.append("[sitl] params must be a table of NAME = number")
        else:
            for key, value in self.sitl.params.items():
                if not re.fullmatch(r"[A-Z][A-Z0-9_]*", str(key)):
                    errors.append(
                        f"[sitl] params: {key!r} is not an ArduPilot parameter "
                        "name"
                    )
                if not _is_number(value):
                    errors.append(
                        f"[sitl] params: {key} must be a number, got {value!r}"
                    )
        return errors

    def _wsl_errors(self) -> list[str]:
        """The problems with the [wsl] values."""
        errors: list[str] = []
        if self.wsl.memory:
            try:
                if self.wsl.memory_mb < 1024:
                    errors.append(
                        "[wsl] memory must be at least 1GB, got "
                        f"{self.wsl.memory!r}"
                    )
            except ConfigError as exc:
                errors.append(f"[wsl] memory: {exc}")
        if not (
            _is_whole_number(self.wsl.processors) and self.wsl.processors >= 0
        ):
            errors.append(
                "[wsl] processors must be a whole number (0 = leave alone), "
                f"got {self.wsl.processors!r}"
            )
        return errors

    def _unreal_errors(self) -> list[str]:
        """The problems with the [unreal] values."""
        errors: list[str] = []
        for name in ("width", "height"):
            value = getattr(self.unreal, name)
            if not (_is_whole_number(value) and 160 <= value <= 16384):
                errors.append(
                    f"[unreal] {name} must be a whole number of pixels "
                    f"(160..16384), got {value!r}"
                )
        grace = self.unreal.close_grace_s
        if not (_is_number(grace) and grace > 0):
            errors.append(
                "[unreal] close_grace_s must be a number of seconds above 0, "
                f"got {grace!r}"
            )
        reach = self.unreal.wsl_reach_timeout_s
        if not (_is_number(reach) and reach >= 0):
            errors.append(
                "[unreal] wsl_reach_timeout_s must be a number of seconds (0 "
                f"= skip the wait), got {reach!r}"
            )
        if self.unreal.priority not in UNREAL_PRIORITIES:
            allowed = ", ".join(UNREAL_PRIORITIES)
            errors.append(
                f"[unreal] priority must be one of {allowed}, "
                f"got {self.unreal.priority!r}"
            )
        for name in ("topic_port", "service_port"):
            value = getattr(self.unreal, name)
            if not (_is_whole_number(value) and 0 < value < 65536):
                errors.append(
                    f"[unreal] {name} must be a TCP port, got {value!r}"
                )
        return errors

    def _argument_list_errors(self) -> list[str]:
        """The settings that must be lists of strings, and are not."""
        errors: list[str] = []
        for section, key in (
            ("unreal", "args"),
            ("sitl", "extra_args"),
            ("scripts", "args"),
        ):
            value = getattr(getattr(self, section), key)
            if not (
                isinstance(value, list)
                and all(isinstance(v, str) for v in value)
            ):
                errors.append(f"[{section}] {key} must be a list of strings")
        return errors


# === LOADING THE FILE =========================================================


# path fields are resolved against the config file's folder
_PATH_FIELDS = {
    "unreal": ("exe",),
    "scene": ("config_dir",),
    "scripts": ("dir",),
}


_SECTIONS: dict[str, type] = {
    "wsl": WslCfg,
    "unreal": UnrealCfg,
    "scene": SceneCfg,
    "sitl": SitlCfg,
    "scripts": ScriptsCfg,
}


def _build_section(name: str, cls: type, raw: dict[str, Any]) -> Any:
    """Make one section's settings object from the raw table; an unknown key is
    an error with a 'did you mean' hint."""
    data = raw.get(name, {})
    if not isinstance(data, dict):
        raise ConfigError(f"[{name}] must be a table")
    known = {f.name for f in fields(cls)}
    for key in data:
        if key not in known:
            hint = difflib.get_close_matches(key, known, n=1)
            suffix = f" (did you mean '{hint[0]}'?)" if hint else ""
            raise ConfigError(f"unknown key '{key}' in [{name}]{suffix}")
    return cls(**data)


def _read_toml(path: Path) -> dict[str, Any]:
    """Read and parse the settings file; a file that cannot be read as TOML is
    a ConfigError that names it."""
    try:
        # utf-8-sig: some Windows editors (and PowerShell's Set-Content) put a
        # byte-order mark first, which the TOML parser would reject with an
        # unhelpful "Invalid statement at line 1"
        return tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"{path}: not a UTF-8 text file ({exc.reason})"
        ) from exc


def _check_section_names(raw: dict[str, Any]) -> None:
    """Refuse a [section] the launcher does not have, with a 'did you mean'
    hint."""
    for section in raw:
        if section not in _SECTIONS:
            hint = difflib.get_close_matches(section, _SECTIONS, n=1)
            suffix = f" (did you mean '[{hint[0]}]'?)" if hint else ""
            raise ConfigError(f"unknown section [{section}]{suffix}")


def _resolve_paths(cfg: Config) -> None:
    """Turn the path settings into absolute paths, relative ones being relative
    to the settings file's folder."""
    for section, names in _PATH_FIELDS.items():
        obj = getattr(cfg, section)
        for key in names:
            relative = Path(getattr(obj, key))
            setattr(obj, key, (cfg.base_dir / relative).resolve())


def load(path: str | Path) -> Config:
    """Read and check mrrsim.toml; relative paths in it are resolved against its
    folder."""
    path = Path(path).resolve()
    raw = _read_toml(path)
    _check_section_names(raw)
    cfg = Config(
        source=path,
        **{
            name: _build_section(name, cls, raw)
            for name, cls in _SECTIONS.items()
        },
    )
    _resolve_paths(cfg)
    cfg.validate()
    return cfg
