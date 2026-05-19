# Known Issue: FreeBSD-Only Base Features

## Pattern
- `fatal error: blacklist.h: No such file or directory`
- `fatal error: sys/audit.h: No such file or directory`
- `fatal error: sys/capsicum.h: No such file or directory`
- `-DUSE_BLACKLIST` in compile command with missing `blacklist.h`
- `undefined reference to 'blacklist_open'`
- `undefined reference to 'cap_enter'`
- Build environment shows `USE_LIBBLACKLIST=yes` or similar FreeBSD-only option enabled

## Cause
FreeBSD base system includes several facilities that do not exist in DragonFlyBSD:

| Feature | FreeBSD Header/Lib | Description |
|---------|-------------------|-------------|
| blacklistd | `blacklist.h`, `libblacklist` | Connection blacklisting daemon |
| Capsicum | `sys/capsicum.h` | Capability-based sandboxing |
| BSM Audit | `sys/audit.h`, `libbsm` | Basic Security Module auditing |
| SCTP | `netinet/sctp.h` | Stream Control Transmission Protocol |
| utmpx | `utmpx.h` | Extended user accounting (partial) |

When a port has an OPTION that enables these features (often ON by default in FreeBSD ports), the build fails on DragonFly because the headers/libraries are missing.

## Fix

### Option 1: Disable the option in Makefile.DragonFly (Preferred)

If the port defines an OPTION for the feature, disable it by default on DragonFly using the `:N` pattern to remove from OPTIONS_DEFAULT:

```makefile
# ports/category/portname/Makefile.DragonFly
OPTIONS_DEFAULT:=	${OPTIONS_DEFAULT:NLIBBLACKLIST}
```

The `:N` modifier removes matching items from the list. Common option names:

| Missing Feature | Likely Option Name(s) |
|-----------------|----------------------|
| blacklistd | `BLACKLIST`, `LIBBLACKLIST` |
| Capsicum | `CAPSICUM` |
| BSM Audit | `AUDIT`, `BSM` |
| SCTP | `SCTP` |
| PulseAudio | `PULSEAUDIO` |
| udev/libudev | `UDEV` |
| systemd/basu | `BASU`, `SYSTEMD` |

Multiple options can be disabled in one line:

```makefile
OPTIONS_DEFAULT:=	${OPTIONS_DEFAULT:NPULSEAUDIO:NUDEV}
```

### Option 2: Add replacement option (when alternative exists)

Sometimes DragonFly has an alternative. Use `:N` to remove the bad option and append the good one:

```makefile
# Remove MIT krb5 from base, use port instead
OPTIONS_DEFAULT:=	${OPTIONS_DEFAULT:NGSSAPI_BASE} GSSAPI_MIT
```

```makefile
# Use ALSA instead of PulseAudio
OPTIONS_DEFAULT:=	${OPTIONS_DEFAULT:NPULSEAUDIO} ALSA
```

### Option 3: Patch source to make feature optional (Last Resort)

If the port hardcodes the feature without an OPTION, create `diffs/patch-*.diff` to wrap the code in `#ifdef` guards or remove the problematic includes.

## Examples
- `net/bsdrcmds`: `OPTIONS_DEFAULT:= ${OPTIONS_DEFAULT:NLIBBLACKLIST}` - disables blacklistd support
- `www/bozohttpd`: `OPTIONS_DEFAULT:= ${OPTIONS_DEFAULT:NBLACKLIST}` - same pattern, different option name
- `security/sudo`: `OPTIONS_DEFAULT:= ${OPTIONS_DEFAULT:NAUDIT}` - disables BSM audit
- `net/yate`: `OPTIONS_DEFAULT:= ${OPTIONS_DEFAULT:NSCTP}` - disables SCTP protocol support
- `x11/waybar`: `OPTIONS_DEFAULT:= ${OPTIONS_DEFAULT:NPULSEAUDIO:NUDEV}` - disables multiple missing features
- `x11-wm/sway`: `OPTIONS_DEFAULT:= ${OPTIONS_DEFAULT:NBASU}` - disables systemd/basu integration

## Triage Classification
This error type is **patchable** when:
1. The error is a missing header/library for a known FreeBSD-only feature
2. The port has an OPTION that controls the feature
3. The feature is not essential to the port's core functionality

Classify as `freebsd-feature` with `high` confidence when the pattern matches.

## Detection Hints
To identify the correct OPTION to disable:
1. Check errors.txt for the missing header (e.g., `blacklist.h`)
2. Search the port's Makefile for related OPTION definitions
3. Look for `-DUSE_*` flags in the failing compile command
4. Check if DeltaPorts already has a fix for similar ports
