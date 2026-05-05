from __future__ import annotations


class DevEnvError(Exception):
    """Base class for expected dev-env failures."""


class UsageError(DevEnvError):
    pass


class ConfigError(DevEnvError):
    pass


class StateError(DevEnvError):
    pass


class MountError(DevEnvError):
    pass


class ProvisionError(DevEnvError):
    pass


class CommandError(DevEnvError):
    pass
