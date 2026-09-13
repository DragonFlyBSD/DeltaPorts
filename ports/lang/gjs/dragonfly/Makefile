PORTNAME=	gjs
DISTVERSION=	1.88.1
CATEGORIES=	lang gnome
MASTER_SITES=	GNOME
DIST_SUBDIR=	gnome

MAINTAINER=	gnome@FreeBSD.org
COMMENT=	GNOME Javascript binding
WWW=		https://gitlab.gnome.org/GNOME/gjs

LICENSE=	MPL11 LGPL20+
LICENSE_COMB=	multi
LICENSE_FILE_MPL11=	${WRKSRC}/LICENSES/MPL-1.1.txt
LICENSE_FILE_LGPL20+ =	${WRKSRC}/LICENSES/LGPL-2.0-or-later.txt

BUILD_DEPENDS=	dbus-run-session:devel/dbus
LIB_DEPENDS=	libmozjs-140.so:lang/spidermonkey140 \
		libffi.so:devel/libffi

USE_GCC_VERSION=	13
USES=		compiler:c++17-lang gettext gnome \
		localbase meson pkgconfig python:build readline tar:xz xorg
USE_GNOME=	cairo glib20 introspection
USE_XORG=	x11 xext
MESON_ARGS=	-Dreadline=enabled \
		-Dprofiler=disabled \
		-Dinstalled_tests=false \
		-Dgobject-introspection-tests:install_dir='' \
		-Dskip_dbus_tests=true \
		-Dskip_gtk_tests=true
USE_LDCONFIG=	yes

CONFIGURE_ENV=	PYTHONDONTWRITEBYTECODE=1
MAKE_ENV=	PYTHONDONTWRITEBYTECODE=1

.include <bsd.port.mk>
