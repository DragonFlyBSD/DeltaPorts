PORTNAME=	gnome-session
DISTVERSION=	50.1
CATEGORIES=	x11 gnome
MASTER_SITES=	GNOME
DIST_SUBDIR=	gnome

MAINTAINER=	gnome@FreeBSD.org
COMMENT=	Session component for the GNOME desktop
WWW=		https://gitlab.gnome.org/GNOME/gnome-session

LICENSE=	GPLv2+
LICENSE_FILE=	${WRKSRC}/COPYING

LIB_DEPENDS=	libgnome-desktop-4.so:x11/gnome-desktop
RUN_DEPENDS=	dbus-daemon:devel/dbus

USES=		compiler:c11 gettext gnome localbase:ldflags meson \
		pkgconfig python:build tar:xz
USE_GNOME=	glib20 gnomedesktop4 libxslt:build

# DragonFly has no systemd. gnome-session 50 upstream made libsystemd/systemd
# mandatory and deleted the non-systemd path; -Dsystemd=false re-enables a
# build without them (see the DragonFly patches: systemd usage is guarded by
# ENABLE_SYSTEMD, session tracking falls back to the inline null backend).
MESON_ARGS=	-Ddocbook=false \
		-Dman=false \
		-Dsystemd=false \
		-Dsystemduserunitdir=${PREFIX}/lib/systemd/user

BINARY_ALIAS=	python3=${PYTHON_VERSION}

PLIST_SUB=	VERSION=${PORTVERSION:C/^([0-9]+)\..*/\1/}

GLIB_SCHEMAS=	org.gnome.SessionManager.gschema.xml

.include <bsd.port.mk>
