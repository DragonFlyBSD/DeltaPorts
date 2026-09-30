/* DragonFly null sd-daemon shim for GDM.
 *
 * Companion to the sd-login shim in this directory: the system is
 * never "booted with systemd", so journald-related fast paths stay
 * disabled and file logging is used instead.
 */
#ifndef DRAGONFLY_COMPAT_SD_DAEMON_H
#define DRAGONFLY_COMPAT_SD_DAEMON_H

static inline int
sd_booted (void)
{
        return 0;
}

#endif /* DRAGONFLY_COMPAT_SD_DAEMON_H */
