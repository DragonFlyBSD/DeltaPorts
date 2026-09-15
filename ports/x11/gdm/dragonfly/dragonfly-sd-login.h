/* DragonFly null sd-login shim for GDM.
 *
 * There is no logind (and no ConsoleKit) on DragonFly. GDM runs with a
 * single static seat "seat0" and performs no session tracking of its
 * own: seat and device handover is the job of seatd/libseat inside the
 * session compositor (mutter), mirroring the gnome-session null-backend
 * approach used elsewhere in the GNOME 50 DragonFly stack.
 *
 * Every query below answers as a loginless system:
 *   - seat0 exists and is graphical, but exposes no VTs to GDM
 *     (VT/console handling stays with the kernel and seatd);
 *   - no session ever exists (-ENXIO / -ENODATA / empty lists);
 *   - callers keep their documented error-path behaviour.
 *
 * sd-login.h contract notes honoured here: out-parameters are only
 * written on success; negative errno is returned on failure; list
 * getters return the item count.
 */
#ifndef DRAGONFLY_COMPAT_SD_LOGIN_H
#define DRAGONFLY_COMPAT_SD_LOGIN_H

#include <errno.h>
#include <stddef.h>
#include <sys/types.h>

/* DragonFly's errno.h has no ENODATA (an XSI/STREAMS errno). GDM compares
 * against -ENODATA in a few sd-login call sites; map it to the closest
 * existing errno so those comparisons stay consistent tree-wide (this
 * header is the only producer of ENODATA values here). */
#ifndef ENODATA
#define ENODATA ENOMSG
#endif

/* --- pid --- */

static inline int
sd_pid_get_session (pid_t pid, char **session)
{
        (void) pid; (void) session;
        return -ENODATA;
}

static inline int
sd_pid_get_cgroup (pid_t pid, char **cgroup)
{
        (void) pid; (void) cgroup;
        return -ENODATA;
}

static inline int
sd_pid_get_user_unit (pid_t pid, char **unit)
{
        (void) pid; (void) unit;
        return -ENODATA;
}

/* --- seat --- */

static inline int
sd_seat_can_graphical (const char *seat)
{
        (void) seat;
        return 1;
}

static inline int
sd_seat_can_tty (const char *seat)
{
        /* No VTs from GDM's point of view: all VT juggling code in the
         * session worker stays dormant; the compositor owns the console
         * through seatd. */
        (void) seat;
        return 0;
}

static inline int
sd_seat_get_active (const char *seat, char **session, uid_t *uid)
{
        (void) seat; (void) session; (void) uid;
        return -ENODATA;
}

static inline int
sd_seat_get_sessions (const char *seat, char ***sessions,
                      uid_t **uids, unsigned int *n_uids)
{
        (void) seat;
        if (sessions != NULL)
                *sessions = NULL;
        if (uids != NULL)
                *uids = NULL;
        if (n_uids != NULL)
                *n_uids = 0;
        return 0;
}

/* --- session --- */

static inline int
sd_session_get_class (const char *session, char **session_class)
{
        (void) session; (void) session_class;
        return -ENXIO;
}

static inline int
sd_session_get_seat (const char *session, char **seat)
{
        (void) session; (void) seat;
        return -ENXIO;
}

static inline int
sd_session_get_service (const char *session, char **service)
{
        (void) session; (void) service;
        return -ENXIO;
}

static inline int
sd_session_get_state (const char *session, char **state)
{
        (void) session; (void) state;
        return -ENXIO;
}

static inline int
sd_session_get_tty (const char *session, char **tty)
{
        (void) session; (void) tty;
        return -ENXIO;
}

static inline int
sd_session_get_type (const char *session, char **type)
{
        (void) session; (void) type;
        return -ENXIO;
}

static inline int
sd_session_get_uid (const char *session, uid_t *uid)
{
        (void) session; (void) uid;
        return -ENXIO;
}

static inline int
sd_session_get_username (const char *session, char **username)
{
        (void) session; (void) username;
        return -ENXIO;
}

static inline int
sd_session_get_vt (const char *session, unsigned int *vtnr)
{
        (void) session; (void) vtnr;
        return -ENXIO;
}

static inline int
sd_session_is_remote (const char *session)
{
        (void) session;
        return -ENXIO;
}

/* --- uid --- */

static inline int
sd_uid_get_display (uid_t uid, char **display)
{
        (void) uid; (void) display;
        return -ENODATA;
}

static inline int
sd_uid_get_sessions (uid_t uid, int require_active, char ***sessions)
{
        (void) uid; (void) require_active;
        if (sessions != NULL)
                *sessions = NULL;
        return 0;
}

#endif /* DRAGONFLY_COMPAT_SD_LOGIN_H */
