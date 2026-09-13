PORTNAME=	gdm
DISTVERSION=	50.1
CATEGORIES=	x11 gnome
MASTER_SITES=	GNOME
DIST_SUBDIR=	gnome

MAINTAINER=	gnome@FreeBSD.org
COMMENT=	GNOME display manager
WWW=		https://gitlab.gnome.org/GNOME/gdm

LICENSE=	GPLv2+
LICENSE_FILE=	${WRKSRC}/COPYING

BUILD_DEPENDS=	itstool:textproc/itstool
LIB_DEPENDS=	libaccountsservice.so:sysutils/accountsservice \
		libgudev-1.0.so:devel/libgudev \
		libjson-glib-1.0.so:devel/json-glib \
		libpolkit-gobject-1.so:sysutils/polkit
RUN_DEPENDS=	dbus-daemon:devel/dbus \
		gnome-session>0:x11/gnome-session

USERS=		gdm
GROUPS=		gdm video

USES=		cpe gettext gnome localbase:ldflags meson pathfix \
		pkgconfig tar:xz
CPE_VENDOR=	gnome
CPE_PRODUCT=	gnome_display_manager
USE_GNOME=	dconf glib20 introspection:build
USE_LDCONFIG=	yes
USE_RC_SUBR=	gdm
USE_GNOME_SUBR=	yes
GLIB_SCHEMAS=	org.gnome.login-screen.gschema.xml \
		15_org.gnome.login-screen.gschema.override

# DragonFly has no logind (and the ConsoleKit route is not used on this
# platform). gdm is built against a null sd-login shim: one static seat0,
# no session tracking. Seat/DRM/input handover happens through seatd and
# libseat inside the user's compositor (mutter), so the VT juggling in the
# session worker is compiled out and the worker provisions
# XDG_RUNTIME_DIR=/tmp/runtime-<uid> itself. Wayland-only: mutter 50 has
# no X11 session code; XWayland still runs X11 apps inside the session.
MESON_ARGS=	-Dat-spi-registryd-dir=${PREFIX}/libexec \
		-Dcustom-conf=${PREFIX}/etc/gdm/custom.conf \
		-Ddefault-pam-config=none \
		-Ddmconfdir=${PREFIX}/share/xsessions \
		-Ddyn-home-dir=/var/cache/gdm \
		-Dgdm-xsession=false \
		-Dgroup=gdm \
		-Dlibaudit=disabled \
		-Dlog-dir=/var/log/gdm \
		-Dpam-mod-dir=${PREFIX}/lib \
		-Dpid-file=/var/run/gdm.pid \
		-Dplymouth=disabled \
		-Drun-dir=/var/run/gdm \
		-Dselinux=disabled \
		-Dsystemd-journal=false \
		-Dsystemdsystemunitdir=no \
		-Dsystemduserunitdir=no \
		-Dworking-dir=/var/cache/gdm \
		-Dx11-support=false

SUB_FILES=	gdm-autologin gdm-launch-environment gdm-password

GNOME_LOCALSTATEDIR=	/var

post-extract:
	@${MKDIR} ${WRKSRC}/common/dragonfly-compat/systemd
	${INSTALL_DATA} ${FILESDIR}/dragonfly-sd-login.h \
		${WRKSRC}/common/dragonfly-compat/systemd/sd-login.h
	${INSTALL_DATA} ${FILESDIR}/dragonfly-sd-daemon.h \
		${WRKSRC}/common/dragonfly-compat/systemd/sd-daemon.h

pre-install:
	${MKDIR} ${STAGEDIR}/var/cache/gdm ${STAGEDIR}/var/log/gdm \
		${STAGEDIR}/var/run/gdm

post-install:
	${INSTALL_SCRIPT} ${FILESDIR}/gnome-session-dragonfly \
		${STAGEDIR}${PREFIX}/libexec/gnome-session-dragonfly
	${MV} ${STAGEDIR}${PREFIX}/etc/gdm/custom.conf \
		${STAGEDIR}${PREFIX}/etc/gdm/custom.conf.sample
	${MV} ${STAGEDIR}${PREFIX}/etc/dbus-1/system.d/gdm.conf \
		${STAGEDIR}${PREFIX}/etc/dbus-1/system.d/gdm.conf.sample
	@${MKDIR} ${STAGEDIR}${PREFIX}/etc/pam.d
	${INSTALL_DATA} ${WRKDIR}/gdm-autologin \
		${STAGEDIR}${PREFIX}/etc/pam.d/gdm-autologin.sample
	${INSTALL_DATA} ${WRKDIR}/gdm-launch-environment \
		${STAGEDIR}${PREFIX}/etc/pam.d/gdm-launch-environment.sample
	${INSTALL_DATA} ${WRKDIR}/gdm-password \
		${STAGEDIR}${PREFIX}/etc/pam.d/gdm-password.sample
	${INSTALL_DATA} ${FILESDIR}/15_org.gnome.login-screen.gschema.override \
		${STAGEDIR}${PREFIX}/share/glib-2.0/schemas/

.include <bsd.port.mk>
