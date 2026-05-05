from __future__ import annotations

from pathlib import Path

from .config import DevEnvConfig
from .state import EnvironmentState


def write_dsynth_config(config: DevEnvConfig, state: EnvironmentState) -> None:
    config_dir = state.root_dir / "etc/dsynth"
    dsynth_root = state.root_dir / "work/dsynth"
    for path in [
        config_dir,
        dsynth_root / "packages/All",
        dsynth_root / "options",
        dsynth_root / "build",
        dsynth_root / "logs",
        state.root_dir / f"work/artifacts/compose/{state.target}",
        state.root_dir / "usr/distfiles",
    ]:
        path.mkdir(parents=True, exist_ok=True)

    (config_dir / "dsynth.ini").write_text(
        f"""[Global Configuration]
profile_selected= DPortsDev

[DPortsDev]
Operating_system= DragonFly
Directory_packages= /work/dsynth/packages
Directory_repository= /work/dsynth/packages/All
Directory_portsdir= /work/artifacts/compose/{state.target}
Directory_options= /work/dsynth/options
Directory_distfiles= /usr/distfiles
Directory_buildbase= /work/dsynth/build
Directory_logs= /work/dsynth/logs
Directory_ccache= disabled
Directory_system= /
Package_suffix= .txz
Number_of_builders= {config.dsynth_builders}
Max_jobs_per_builder= {config.dsynth_jobs}
Display_with_ncurses= true
"""
    )
    (config_dir / "DPortsDev-make.conf").write_text("DISTDIR=/usr/distfiles\nWRKDIRPREFIX=/construction\n")
